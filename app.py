from flask import Flask, render_template, request, jsonify, redirect, session, flash, send_file, abort, Response, url_for
import sqlite3, os, hashlib, subprocess, sys, socket, base64, hmac, uuid, asyncio, io, wave
from functools import wraps
from datetime import date, datetime
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except Exception:
    TfidfVectorizer = None
    cosine_similarity = None
from werkzeug.utils import secure_filename
from rag_engine import answer_with_gemini, gemini_config_status

# ── Auto-start WhatsApp Bot (Node.js) server ──────────────────
import time as _time
import urllib.request as _urlreq
import json as _json

def _is_port_open(port):
    """Check if port is already in use (bot already running)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def _bot_health():
    """Check bot health via /health endpoint. Returns status string or None."""
    try:
        req = _urlreq.Request('http://localhost:3001/health')
        with _urlreq.urlopen(req, timeout=3) as resp:
            data = _json.loads(resp.read().decode('utf-8'))
            return data.get('status', None)
    except Exception:
        return None

def _restart_bot_api():
    """Call /restart on a running but stuck bot."""
    try:
        req = _urlreq.Request('http://localhost:3001/restart', method='POST',
                              data=b'{}', headers={'Content-Type': 'application/json'})
        with _urlreq.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read().decode('utf-8'))
            return data.get('success', False)
    except Exception:
        return False

def _kill_old_bot():
    """Kill any existing node server.js processes on port 3001 (Windows)."""
    try:
        result = subprocess.run(
            ['powershell', '-Command',
             "Get-NetTCPConnection -LocalPort 3001 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess"],
            capture_output=True, text=True, timeout=5
        )
        pids = set(result.stdout.strip().split('\n')) if result.stdout.strip() else set()
        for pid in pids:
            pid = pid.strip()
            if pid and pid.isdigit() and int(pid) > 0:
                try:
                    subprocess.run(['taskkill', '/F', '/PID', pid],
                                   capture_output=True, timeout=5)
                    print(f"  Killed stale process PID {pid}.")
                except Exception:
                    pass
        if pids:
            _time.sleep(2)  # Wait for port to free up
    except Exception as e:
        print(f"  (Could not check old processes: {e})")

def _clean_wa_stale_locks():
    """Remove stale Chromium lock files that block bot re-initialization."""
    import glob
    session_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wa_bot', 'wa_session')
    lock_names = ['SingletonLock', 'SingletonCookie', 'SingletonSocket']
    if os.path.exists(session_dir):
        for lock_name in lock_names:
            for lock_path in glob.glob(os.path.join(session_dir, '**', lock_name), recursive=True):
                try:
                    os.remove(lock_path)
                    print(f"  Cleaned stale lock: {lock_path}")
                except Exception:
                    pass

def _start_wa_bot():
    bot_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wa_bot')
    if not os.path.exists(os.path.join(bot_dir, 'server.js')):
        print("WARNING: wa_bot/server.js not found -- skipping bot start.")
        return

    # Always clean stale Chromium lock files before starting
    _clean_wa_stale_locks()

    if _is_port_open(3001):
        # Port is open — check if bot is healthy
        status = _bot_health()
        if status == 'connected':
            print("OK: WhatsApp Bot already running and connected on port 3001.")
            return
        elif status == 'qr_ready' or status == 'initializing':
            print(f"OK: WhatsApp Bot running (status: {status}) on port 3001.")
            return
        elif status == 'error' or status == 'disconnected':
            # Bot is stuck — try to restart via API
            print(f"WARNING: WhatsApp Bot is in '{status}' state. Sending restart command...")
            if _restart_bot_api():
                print("OK: WhatsApp Bot restart command sent successfully.")
                return
            else:
                # API restart failed — kill and restart fresh
                print("WARNING: API restart failed. Killing old process...")
                _kill_old_bot()
        else:
            # Unknown status — something else on port 3001?
            print(f"WARNING: Unknown status '{status}' on port 3001. Attempting fresh start...")
            _kill_old_bot()
    
    # Start node server.js as a background process
    try:
        # Log file for debugging (last 1 restart log)
        log_file = os.path.join(bot_dir, 'bot.log')
        log_f = open(log_file, 'w', encoding='utf-8')
        
        kwargs = {}
        if sys.platform == 'win32':
            kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            kwargs['start_new_session'] = True
        proc = subprocess.Popen(
            ['node', 'server.js'],
            cwd=bot_dir,
            stdout=log_f,
            stderr=log_f,
            **kwargs
        )
        print(f"OK: WhatsApp Bot server started (PID {proc.pid}) on port 3001.")
        
        # Wait a moment and verify the process started properly
        _time.sleep(3)
        if _is_port_open(3001):
            print("OK: WhatsApp Bot port 3001 confirmed open.")
        else:
            print("INFO: WhatsApp Bot starting up... (may take a few seconds)")
    except FileNotFoundError:
        print("WARNING: Node.js not found! Please install Node.js to use WhatsApp Bot.")
    except Exception as e:
        print(f"WARNING: Could not start WhatsApp Bot: {e}")

_start_wa_bot()
# ─────────────────────────────────────────────────────────────

app = Flask(__name__, template_folder="new_templates")
app.secret_key = "x7k$9mP!qR2wZ@vL"

def load_local_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except Exception as e:
        print(f"ENV load warning: {e}")

load_local_env()

@app.after_request
def add_chat_cors_headers(response):
    if request.path.startswith("/chat") or request.path.startswith("/livekit") or request.path.startswith("/tts"):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

# ── Library config (embedded — no separate server needed) ──
LIB_DB      = os.path.join(os.path.dirname(__file__), "database.db")
LIB_UPLOAD  = os.path.join(os.path.dirname(__file__), "static", "lib_uploads", "ebooks")
LIB_ALLOWED = {'pdf', 'epub', 'doc', 'docx', 'ppt', 'pptx'}

def get_departments():
    conn = get_lib_db()
    c = conn.cursor()
    try:
        c.execute("SELECT * FROM lib_departments ORDER BY name")
        depts = c.fetchall()
        c.execute("SELECT * FROM lib_subjects ORDER BY name")
        subs = c.fetchall()
        sub_keys = [desc[0] for desc in c.description] if c.description else []
        result = []
        for r in depts:
            dept_icon  = r['icon']  or '📁'
            dept_color = r['color'] or '#2e86de'
            d_subs = []
            for s in subs:
                if s['dept_id'] != r['id']:
                    continue
                s_icon  = (s['icon']  if 'icon'  in sub_keys else None) or '📄'
                s_color = (s['color'] if 'color' in sub_keys else None) or dept_color
                d_subs.append({'id': s['id'], 'name': s['name'], 'icon': s_icon, 'color': s_color})
            result.append({'id': r['id'], 'name': r['name'], 'icon': dept_icon,
                           'color': dept_color, 'heading': r['heading'] or 'DEPARTMENTS',
                           'subjects': d_subs})
        return result
    except Exception as e:
        print(f"get_departments error: {e}")
        return []
    finally:
        conn.close()


def get_grouped_departments():
    depts = get_departments()
    groups = []
    seen = set()
    for d in depts:
        h = d['heading']
        if h not in seen:
            seen.add(h)
            groups.append({'heading': h, 'depts': []})
        for g in groups:
            if g['heading'] == h:
                g['depts'].append(d)
                break
    return groups

@app.context_processor
def inject_grouped_departments():
    return dict(grouped_departments=get_grouped_departments(), departments=get_departments())

def hash_pw(pw):  return hashlib.sha256(pw.encode()).hexdigest()
def lib_now():    return datetime.now().strftime('%Y-%m-%d %H:%M')
def lib_today():  return datetime.now().strftime('%Y-%m-%d')

def get_lib_db():
    conn = sqlite3.connect(LIB_DB)
    conn.row_factory = sqlite3.Row
    return conn

def lib_allowed_file(fn): return '.' in fn and fn.rsplit('.',1)[1].lower() in LIB_ALLOWED

def login_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if 'lib_user' not in session:
            return redirect('/library/login')
        return f(*a, **kw)
    return dec

def admin_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if 'lib_user' not in session:
            return redirect('/library/login')
        if session['lib_user']['role'] not in ('admin', 'teacher'):
            abort(403)
        return f(*a, **kw)
    return dec

def only_lib_admin(f):
    @wraps(f)
    def dec(*a, **kw):
        if 'lib_user' not in session:
            return redirect('/library/login')
        if session['lib_user']['role'] != 'admin':
            abort(403)
        return f(*a, **kw)
    return dec


GALLERY_FOLDER     = os.path.join('static', 'gallery')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ---- Cache TF-IDF model ----
vectorizer = None
q_vectors = None
questions_cache = []
answers_cache = []

def load_model():
    global vectorizer, q_vectors, questions_cache, answers_cache
    data = get_data()
    questions_cache = [i[0] for i in data]
    answers_cache = [i[1] for i in data]
    if questions_cache and TfidfVectorizer is not None:
        vectorizer = TfidfVectorizer()
        q_vectors = vectorizer.fit_transform(questions_cache)

def local_chat_reply(user_input):
    if not questions_cache:
        return "Database is empty. Please contact admin."

    if vectorizer is not None and q_vectors is not None and cosine_similarity is not None:
        u_vector = vectorizer.transform([user_input])
        similarity = cosine_similarity(u_vector, q_vectors)
        index = int(similarity.argmax())
        confidence = float(similarity[0, index])
        if confidence > 0.35:
            return answers_cache[index]
        return "Sorry, I don't have information on that. Please contact the college helpdesk."

    user_words = set(user_input.lower().split())
    best_index, best_score = 0, 0
    for i, question in enumerate(questions_cache):
        q_words = set(question.lower().split())
        score = len(user_words & q_words) / max(len(user_words), 1)
        if score > best_score:
            best_index, best_score = i, score
    if best_score > 0.25:
        return answers_cache[best_index]
    return "Sorry, I don't have information on that. Please contact the college helpdesk."

def get_data():
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute("SELECT question, answer FROM qa")
        data = cursor.fetchall()
        conn.close()
        return data
    except Exception as e:
        print(f"DB Error: {e}")
        return []

def get_all_qa():
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, question, answer, COALESCE(source,'manual') FROM qa")
        data = cursor.fetchall()
        conn.close()
        return data
    except Exception as e:
        print(f"DB Error: {e}")
        return []

def get_all_grievances():
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM grievances ORDER BY id DESC")
        cols = [column[0] for column in cursor.description]
        data = [dict(zip(cols, row)) for row in cursor.fetchall()]
        conn.close()
        return data
    except Exception as e:
        print(f"DB Error fetching grievances: {e}")
        return []

def get_all_admissions():
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM admissions ORDER BY id DESC")
        cols = [column[0] for column in cursor.description]
        data = [dict(zip(cols, row)) for row in cursor.fetchall()]
        conn.close()
        return data
    except Exception as e:
        print(f"DB Error fetching admissions: {e}")
        return []


# ── Chat Log helpers ────────────────────────────────────────
UNANSWERED_PHRASES = [
    "sorry, i don't have",
    "sorry, i don't know",
    "i don't have information",
    "please contact",
    "database is empty",
    "something went wrong",
]

def _is_unanswered(reply):
    r = reply.lower()
    return any(p in r for p in UNANSWERED_PHRASES)

def save_chat_log(question, answer, source="bot"):
    """Save a chat Q&A to chat_log. Skips duplicates."""
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        # Duplicate check
        cursor.execute(
            "SELECT id FROM chat_log WHERE LOWER(TRIM(question))=LOWER(TRIM(?))",
            (question,)
        )
        if cursor.fetchone():
            conn.close()
            return  # already logged
        status = "pending" if _is_unanswered(answer) else "answered"
        cursor.execute(
            "INSERT INTO chat_log (question, answer, source, status, asked_at) VALUES (?,?,?,?,?)",
            (question, answer, source,
             status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"ChatLog Save Error: {e}")

def auto_save_to_qa(question, answer):
    """If bot gave a real answer, also save to qa table (no duplicate)."""
    if _is_unanswered(answer):
        return  # don't save unanswered to qa
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM qa WHERE LOWER(TRIM(question))=LOWER(TRIM(?))",
            (question,)
        )
        if cursor.fetchone():
            conn.close()
            return  # already in qa
        cursor.execute(
            "INSERT INTO qa (question, answer, source) VALUES (?,?,?)",
            (question, answer, 'auto')
        )
        conn.commit()
        conn.close()
        load_model()  # retrain TF-IDF
        print(f"[AutoSave] New Q&A learned: {question[:60]}")
    except Exception as e:
        print(f"AutoSave QA Error: {e}")

# ---- Routes ----

@app.route("/")
def home():
    notices = list(get_all_notices())
    notices = notices[:5]
    gallery_photos = get_all_gallery()  # (id, title, filename, category)
    hp_events = get_hp_events()[:5]
    hp_faculty = get_hp_faculty()
    hp_pstats = get_hp_placement_stats()
    hp_companies = get_hp_companies()
    hp_alumni = get_hp_alumni()
    return render_template("index.html", notices=notices, gallery_photos=gallery_photos,
        hp_events=hp_events, hp_faculty=hp_faculty, hp_pstats=hp_pstats,
        hp_companies=hp_companies, hp_alumni=hp_alumni)

@app.route("/chat", methods=["POST"])
def chat():
    payload = request.get_json(silent=True) or {}
    user_input = (
        request.form.get("msg")
        or payload.get("msg")
        or payload.get("question")
        or ""
    ).strip()

    if not user_input:
        return jsonify({"response": "Please type a message."})

    gemini_status = gemini_config_status(os.path.dirname(os.path.abspath(__file__)))

    try:
        ai_reply = answer_with_gemini(
            user_input,
            base_dir=os.path.dirname(os.path.abspath(__file__)),
            db_path=LIB_DB,
        )
        if ai_reply:
            # ── Auto-save Gemini answer ──
            save_chat_log(user_input, ai_reply, source="gemini")
            auto_save_to_qa(user_input, ai_reply)
            return jsonify({"response": ai_reply, "source": "gemini"})
    except Exception as e:
        print(f"Gemini Chat Error: {e}")

    # Lazy-load the model if not already loaded
    if not questions_cache or vectorizer is None or q_vectors is None:
        load_model()

    # Re-check after attempting to load
    if not questions_cache:
        return jsonify({"response": "Database is empty. Please contact admin."})

    try:
        reply = local_chat_reply(user_input)
    except Exception as e:
        print(f"Chat Error: {e}")
        reply = "Something went wrong. Please try again."

    # ── Auto-save local reply ──
    save_chat_log(user_input, reply, source="local")
    auto_save_to_qa(user_input, reply)

    return jsonify({
        "response": reply,
        "source": "local",
        "gemini": gemini_status,
    })

@app.route("/chat/status")
def chat_status():
    return jsonify({
        "gemini": gemini_config_status(os.path.dirname(os.path.abspath(__file__))),
        "qa_count": len(get_data()),
    })

def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _livekit_token(api_key, api_secret, room, identity, name):
    now = int(_time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": api_key,
        "sub": identity,
        "name": name,
        "nbf": now - 10,
        "exp": now + 60 * 60,
        "video": {
            "room": room,
            "roomJoin": True,
            "canPublish": True,
            "canPublishData": True,
            "canSubscribe": True,
        },
    }
    signing_input = (
        _b64url(_json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64url(_json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    )
    signature = hmac.new(
        api_secret.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return signing_input + "." + _b64url(signature)

@app.route("/livekit/token")
def livekit_token():
    api_url = os.environ.get("LIVEKIT_URL", "").strip()
    api_key = os.environ.get("LIVEKIT_API_KEY", "").strip()
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "").strip()
    if not api_url or not api_key or not api_secret:
        return jsonify({"error": "LiveKit is not configured."}), 500

    room = request.args.get("room", "contai-nova-voice").strip() or "contai-nova-voice"
    identity = request.args.get("identity", "").strip() or f"student-{uuid.uuid4().hex[:8]}"
    name = request.args.get("name", "Student").strip() or "Student"
    return jsonify({
        "url": api_url,
        "room": room,
        "identity": identity,
        "token": _livekit_token(api_key, api_secret, room, identity, name),
    })

async def _make_livekit_tts_wav(text):
    # pyrefly: ignore [missing-import]
    from livekit.agents import inference

    model = os.environ.get("LIVEKIT_TTS_MODEL", "inworld/inworld-tts-2").strip()
    voice = os.environ.get("LIVEKIT_TTS_VOICE", "ashley").strip()
    language = os.environ.get("LIVEKIT_TTS_LANGUAGE", "en").strip()

    tts = inference.TTS(model=model, voice=voice, language=language)
    stream = tts.synthesize(text)
    pcm_chunks = []
    sample_rate = 24000
    channels = 1
    async for audio in stream:
        frame = audio.frame
        sample_rate = frame.sample_rate
        channels = frame.num_channels
        pcm_chunks.append(bytes(frame.data))
    await tts.aclose()

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(pcm_chunks))
    return buf.getvalue()

@app.route("/tts/livekit", methods=["POST", "OPTIONS"])
def livekit_tts():
    if request.method == "OPTIONS":
        return ("", 204)
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or request.form.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Text is required."}), 400
    if len(text) > 1200:
        text = text[:1200]
    try:
        audio = asyncio.run(_make_livekit_tts_wav(text))
        return Response(audio, mimetype="audio/wav")
    except Exception as e:
        print(f"LiveKit TTS error: {e}")
        return jsonify({"error": "LiveKit TTS failed, using browser fallback."}), 502

@app.route("/api/admissions/send_otp", methods=["POST"])
def send_adm_otp():
    data = request.get_json()
    if not data or not data.get('mobile'):
        return jsonify({'success': False, 'message': 'Mobile number is required'})

    import random
    otp = str(random.randint(100000, 999999))
    mobile = data.get('mobile').strip()
    name = data.get('name', 'Student').strip()

    if 'adm_otps' not in session:
        session['adm_otps'] = {}
    session['adm_otps'][mobile] = otp
    session.modified = True

    msg = f"*Contai Polytechnic Admissions* 🎓\n\nHello {name},\nYour Admission Application OTP is: *{otp}*\n\n_Do not share this with anyone._"
    try:
        import urllib.request, json
        req = urllib.request.Request('http://localhost:3001/send',
            data=json.dumps({'phone': mobile, 'message': msg}).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            if res_data.get('success'):
                return jsonify({'success': True})
            else:
                return jsonify({'success': False, 'message': 'Failed to send WhatsApp message. Is Bot connected?'})
    except Exception as e:
        print("WhatsApp Bot Error:", e)
        return jsonify({'success': False, 'message': 'WhatsApp Bot service is offline!'})

@app.route("/api/submit_admission", methods=["POST"])
def submit_admission():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "No data provided"}), 400
    
    mobile = data.get('mobile', '').strip()
    submitted_otp = data.get('otp', '').strip()
    
    stored_otps = session.get('adm_otps', {})
    if mobile not in stored_otps or stored_otps[mobile] != submitted_otp:
        return jsonify({"success": False, "message": "Invalid or expired OTP!"}), 400

    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO admissions (name, email, mobile, level, discipline, course, program, auth_check)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get('name', ''),
            data.get('email', ''),
            mobile,
            data.get('level', ''),
            data.get('discipline', ''),
            data.get('course', ''),
            data.get('program', ''),
            1 if data.get('auth_check') else 0
        ))
        conn.commit()
        conn.close()
        
        # Clear the OTP after successful use
        del session['adm_otps'][mobile]
        session.modified = True
        
        return jsonify({"success": True, "message": "Application submitted successfully"})
    except Exception as e:
        print(f"Error submitting admission: {e}")
        return jsonify({"success": False, "message": "Database error"}), 500

# ---- ADMIN ----

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")

        try:
            conn = sqlite3.connect("database.db")
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM admin WHERE username=? AND password=?", (u, p))
            result = cursor.fetchone()
            conn.close()

            if result:
                session["admin"] = True
                return redirect("/admin")
            else:
                flash("Invalid username or password!", "error")
        except Exception as e:
            print(f"Login Error: {e}")
            flash("Something went wrong. Please try again.", "error")

    return render_template("admin_login.html")

@app.route("/admin", methods=["GET", "POST"])
def admin():
    # Allow main admin OR library teacher/admin
    is_main_admin = "admin" in session
    is_lib_teacher = (
        "lib_user" in session and
        session["lib_user"].get("role") in ("admin", "teacher")
    )
    if not is_main_admin and not is_lib_teacher:
        return redirect("/login")

    if request.method == "POST" and is_main_admin:
        q = request.form.get("question", "").strip()
        a = request.form.get("answer", "").strip()

        if q and a:
            try:
                conn = sqlite3.connect("database.db")
                cursor = conn.cursor()
                # ── Duplicate check (case-insensitive) ──
                cursor.execute("SELECT id FROM qa WHERE LOWER(TRIM(question))=LOWER(TRIM(?))", (q,))
                if cursor.fetchone():
                    conn.close()
                    flash("⚠️ This question already exists in the database!", "error")
                else:
                    cursor.execute("INSERT INTO qa (question, answer, source) VALUES (?,?,?)", (q, a, 'manual'))
                    conn.commit()
                    conn.close()
                    load_model()
                    flash("✅ Q&A added successfully!", "success")
            except Exception as e:
                print(f"Admin Error: {e}")
                flash("❌ Failed to add Q&A. Try again.", "error")
        else:
            flash("⚠️ Both question and answer are required!", "error")

    qa_list = get_all_qa()
    gallery_images = get_gallery_for_admin()
    hp_events = get_hp_events()
    hp_faculty = get_hp_faculty()
    hp_pstats = get_hp_placement_stats()
    hp_companies = get_hp_companies()
    hp_alumni = get_hp_alumni()
    teacher_mode = not is_main_admin and is_lib_teacher
    notices = get_all_notices()
    grievances = get_all_grievances()
    admissions = get_all_admissions()
    return render_template("admin.html", qa_list=qa_list, gallery_images=gallery_images,
        hp_events=hp_events, hp_faculty=hp_faculty, hp_pstats=hp_pstats,
        hp_companies=hp_companies, hp_alumni=hp_alumni,
        teacher_mode=teacher_mode, notices=notices, grievances=grievances, admissions=admissions)

@app.route("/view_all")
def view_all():
    if "admin" not in session:
        return redirect("/login")

    qa_list = get_all_qa()
    return render_template("view_all.html", qa_list=qa_list)


@app.route("/admin/chat-log")
def admin_chat_log():
    """Show all chat conversations logged from the chatbot."""
    if "admin" not in session:
        return redirect("/login")
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, question, answer, source, status, asked_at FROM chat_log ORDER BY id DESC"
        )
        logs = cursor.fetchall()
        conn.close()
        pending_count = sum(1 for l in logs if l[4] == "pending")
        return render_template("admin_chat_log.html", logs=logs, pending_count=pending_count)
    except Exception as e:
        print(f"Chat Log Error: {e}")
        flash("❌ Could not load chat log.", "error")
        return redirect("/admin")


@app.route("/admin/chat-log/approve/<int:log_id>", methods=["POST"])
def approve_chat_log(log_id):
    """Admin writes an answer for a pending question and adds it to qa table."""
    if "admin" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    data = request.get_json(silent=True) or {}
    answer = (data.get("answer") or "").strip()
    if not answer:
        return jsonify({"success": False, "error": "Answer is required!"})

    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        # Get the question
        cursor.execute("SELECT question FROM chat_log WHERE id=?", (log_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "error": "Log entry not found."})
        question = row[0]

        # Duplicate check in qa
        cursor.execute(
            "SELECT id FROM qa WHERE LOWER(TRIM(question))=LOWER(TRIM(?))", (question,)
        )
        if not cursor.fetchone():
            cursor.execute("INSERT INTO qa (question, answer, source) VALUES (?,?,?)", (question, answer, 'approved'))

        # Mark log as answered
        cursor.execute(
            "UPDATE chat_log SET answer=?, status='answered' WHERE id=?", (answer, log_id)
        )
        conn.commit()
        conn.close()
        load_model()
        return jsonify({"success": True, "message": "Added to Q&A database!"})
    except Exception as e:
        print(f"Approve Chat Log Error: {e}")
        return jsonify({"success": False, "error": "Database error. Try again."})


@app.route("/admin/chat-log/delete/<int:log_id>")
def delete_chat_log(log_id):
    """Delete a chat log entry."""
    if "admin" not in session:
        return redirect("/login")
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chat_log WHERE id=?", (log_id,))
        conn.commit()
        conn.close()
        flash("🗑️ Log entry deleted!", "success")
    except Exception as e:
        print(f"Delete Chat Log Error: {e}")
        flash("❌ Failed to delete log entry.", "error")
    return redirect("/admin/chat-log")

@app.route("/mechanical")
def mechanical():
    return render_template("mechanical.html")

@app.route("/cst")
def cst():
    return render_template("cst.html")

@app.route("/electrical")
def electrical():
    return render_template("electrical.html")

@app.route("/chemical")
def chemical():
    return render_template("chemical.html")

@app.route("/electronics")
def electronics():
    return render_template("electronics.html")

@app.route("/civil")
def civil():
    return render_template("civil.html")

@app.route("/pharmacy")
def pharmacy():
    return render_template("pharmacy.html")

@app.route("/principal")
def principal():
    return render_template("principal.html")

@app.route("/college-profile")
def college_profile():
    return render_template("college_profile.html")

@app.route("/mission-vision")
def mission_vision():
    return render_template("mission_vision.html")

@app.route("/secretary")
def secretary():
    return render_template("secretary.html")

@app.route("/vocational-nodal")
def vocational_nodal():
    return render_template("vocational_nodal.html")

@app.route("/exam-cell")
def exam_cell():
    return render_template("exam_cell.html")

@app.route("/anti-ragging")
def anti_ragging():
    return render_template("anti_ragging.html")

@app.route("/about-tp-cell")
def about_tp_cell():
    return render_template("placement.html")

@app.route("/tpo-desk")
def tpo_desk():
    return render_template("tpo_desk.html")

@app.route("/tp-cell")
def tp_cell():
    return render_template("tp_cell.html")

@app.route("/industry-partnerships")
def industry_partnerships():
    return render_template("industry_partnerships.html")

@app.route("/academic-calendar")
def academic_calendar():
    return render_template("academic_calendar.html")

@app.route("/scholarship")
def scholarship():
    return render_template("scholarship.html")

@app.route("/hostel")
def hostel():
    return render_template("hostel.html")

@app.route("/canteen")
def canteen():
    return render_template("canteen.html")

@app.route("/academic-holiday")
def academic_holiday():
    return render_template("academic_holiday.html")

@app.route("/admission")
def admission_process():
    return render_template("admission.html")

@app.route("/voice")
def voice_assistant():
    return render_template("voice_assistant.html")

@app.route("/virtual-tour")
def virtual_tour():
    return render_template("virtual_tour.html")


# ════════════════════════════════════════════════════════════
# ──────────────  LIBRARY (embedded — no separate server)  ──
# ════════════════════════════════════════════════════════════

@app.route('/library/login', methods=['GET', 'POST'])
def lib_login():
    if 'lib_user' in session:
        role = session['lib_user'].get('role', 'student')
        if role in ('admin', 'teacher'):
            return redirect('/library/admin')
        return redirect('/library')
    if request.method == 'POST':
        uid = request.form['user_id'].strip().upper()
        pw  = request.form['password']
        conn = get_lib_db(); c = conn.cursor()
        # First check if user exists with matching password (any status)
        c.execute("SELECT * FROM lib_users WHERE (UPPER(user_id)=? OR UPPER(reg_no)=?) AND password=?",
                  (uid, uid, hash_pw(pw)))
        u = c.fetchone(); conn.close()
        if u:
            if u['status'] == 'pending':
                flash('⏳ Your account has not been approved by the Admin yet. Please wait.', 'error')
                return render_template('library/login.html')
            elif u['status'] == 'inactive':
                flash('🚫 Your account has been deactivated. Please contact the Admin.', 'error')
                return render_template('library/login.html')
            # status == 'active'
            session['lib_user'] = {'id': u['id'], 'name': u['name'], 'uid': u['user_id'],
                                   'role': u['role'], 'dept': u['dept']}
            # Admin ও Teacher সরাসরি admin dashboard এ যাবে
            if u['role'] in ('admin', 'teacher'):
                return redirect('/library/admin')
            return redirect('/library')
        flash('❌ Invalid ID or Password!', 'error')
    return render_template('library/login.html')

@app.route('/library/register-request-otp', methods=['POST'])
def lib_register_request_otp():
    data = request.get_json()
    name   = data.get('name', '').strip()
    reg_no = data.get('reg_no', '').strip().upper()
    dept   = data.get('dept', '')
    email  = data.get('email', '').strip()
    phone  = data.get('phone', '').strip()
    pw     = data.get('password', '')
    pw2    = data.get('confirm_password', '')
    
    if not phone or not phone.isdigit() or len(phone) != 10:
        return jsonify({'success': False, 'error': 'Please enter a valid 10-digit mobile number!'})
        
    import re
    if not email or not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        return jsonify({'success': False, 'error': 'Please enter a valid email address!'})
        
    if pw != pw2:
        return jsonify({'success': False, 'error': 'Passwords do not match!'})
    if len(pw) < 6:
        return jsonify({'success': False, 'error': 'Password must be at least 6 characters long!'})
        
    conn = get_lib_db(); c = conn.cursor()
    c.execute("SELECT id FROM lib_users WHERE UPPER(user_id)=? OR UPPER(reg_no)=?", (reg_no, reg_no))
    if c.fetchone():
        conn.close()
        return jsonify({'success': False, 'error': f'Registration No. "{reg_no}" is already registered!'})
    conn.close()
    
    import random, time
    otp = str(random.randint(100000, 999999))
    session['pending_reg'] = {
        'name': name, 'reg_no': reg_no, 'dept': dept, 'email': email, 
        'phone': phone, 'pw': pw, 'otp': otp, 'expires': time.time() + 600
    }
    
    msg = f"*Contai Polytechnic Library* 📚\n\nHello {name},\nYour Library Registration OTP is: *{otp}*\n\n_Do not share this with anyone._"
    try:
        import urllib.request, json
        req = urllib.request.Request('http://localhost:3001/send', 
            data=json.dumps({'phone': phone, 'message': msg}).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            if res_data.get('success'):
                return jsonify({'success': True})
            else:
                return jsonify({'success': False, 'error': 'Failed to send WhatsApp message. Is Bot connected?'})
    except Exception as e:
        print("WhatsApp Bot Error:", e)
        return jsonify({'success': False, 'error': 'WhatsApp Bot service is offline!'})

@app.route('/library/register', methods=['GET', 'POST'])
def lib_register():
    if 'lib_user' in session:
        return redirect('/library')
    if request.method == 'POST':
        otp = request.form.get('otp', '').strip()
        reg_data = session.get('pending_reg')
        
        if not reg_data:
            flash('Session expired. Please register again!', 'error')
            return redirect('/library/login')
            
        import time
        if time.time() > reg_data['expires']:
            flash('OTP expired! Please register again.', 'error')
            session.pop('pending_reg', None)
            return redirect('/library/login')
            
        if reg_data['otp'] != otp:
            flash('Invalid OTP!', 'error')
            return redirect('/library/login')
            
        conn = get_lib_db(); c = conn.cursor()
        try:
            # Auto-generate LIBCON Library ID
            import random as _rnd
            reg_no = reg_data['reg_no']
            suffix = reg_no[-4:] if len(reg_no) >= 4 else reg_no
            lib_id = f"LIBCON{suffix}{_rnd.randint(10, 99)}"
            # Ensure unique
            while True:
                c.execute("SELECT id FROM lib_users WHERE UPPER(user_id)=?", (lib_id.upper(),))
                if not c.fetchone():
                    break
                lib_id = f"LIBCON{suffix}{_rnd.randint(10, 99)}"
            
            c.execute("INSERT INTO lib_users(name,user_id,reg_no,password,role,dept,email,phone,status,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (reg_data['name'], lib_id, reg_no, hash_pw(reg_data['pw']), 'student', reg_data['dept'], reg_data['email'], reg_data['phone'], 'pending', lib_today()))
            conn.commit()
            
            if reg_data['phone']:
                msg = f"*Contai Polytechnic Library* 📚\n\nHello {reg_data['name']},\nYour registration request has been submitted! ⏳\n\n*Library ID:* {lib_id}\n*Reg. No:* {reg_no}\n\n_Your account is pending approval by the Library Admin. You will be notified once approved._"
                try:
                    import urllib.request, json
                    req = urllib.request.Request('http://localhost:3001/send', 
                        data=json.dumps({'phone': reg_data['phone'], 'message': msg}).encode('utf-8'),
                        headers={'Content-Type': 'application/json'}
                    )
                    with urllib.request.urlopen(req) as response:
                        pass
                except Exception as e:
                    print("WhatsApp Bot Error on Self Registration:", e)

            flash(f'✅ Registration successful! Library ID: {lib_id} — You can login once the Admin approves your account.', 'success')
            session.pop('pending_reg', None)
            return redirect('/library/login')
        except sqlite3.IntegrityError:
            flash(f'Registration No. "{reg_data["reg_no"]}" is already registered!', 'error')
        finally:
            conn.close()
            
    return render_template('library/login.html', departments=get_departments())

import random, time, urllib.request, json

@app.route('/library/request-otp', methods=['POST'])
def lib_request_otp():
    data = request.get_json()
    uid = data.get('user_id', '').strip().upper()
    email = data.get('email', '').strip()
    
    conn = get_lib_db(); c = conn.cursor()
    c.execute("SELECT phone, name FROM lib_users WHERE UPPER(user_id)=? AND email=? AND status='active'", (uid, email))
    u = c.fetchone()
    conn.close()
    
    if not u:
        return jsonify({'success': False, 'error': 'ID or Email does not match any active user!'})
    
    phone = u['phone']
    if not phone:
        return jsonify({'success': False, 'error': 'No registered mobile number found for this user!'})
    
    otp = str(random.randint(100000, 999999))
    session['reset_otp'] = {'otp': otp, 'user_id': uid, 'expires': time.time() + 600}
    
    msg = f"*Contai Polytechnic Library* 📚\n\nHello {u['name']},\nYour Password Reset OTP is: *{otp}*\n\n_Do not share this with anyone._"
    
    try:
        req = urllib.request.Request('http://localhost:3001/send', 
            data=json.dumps({'phone': phone, 'message': msg}).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            if res_data.get('success'):
                return jsonify({'success': True})
            else:
                return jsonify({'success': False, 'error': 'Failed to send WhatsApp message. Is Bot connected?'})
    except Exception as e:
        return jsonify({'success': False, 'error': 'WhatsApp Bot service is offline!'})

@app.route('/library/reset-password-otp', methods=['POST'])
def lib_reset_password_otp():
    uid = request.form.get('user_id', '').strip().upper()
    otp = request.form.get('otp', '').strip()
    new_pw = request.form.get('new_password', '')
    
    if len(new_pw) < 6:
        flash('Password কমপক্ষে ৬ অক্ষরের হতে হবে!', 'error')
        return redirect('/library/login')
        
    reset_data = session.get('reset_otp')
    if not reset_data or reset_data['user_id'] != uid:
        flash('Session expired. Please request OTP again!', 'error')
        return redirect('/library/login')
        
    if time.time() > reset_data['expires']:
        flash('OTP expired! Please request again.', 'error')
        return redirect('/library/login')
        
    if reset_data['otp'] != otp:
        flash('Invalid OTP!', 'error')
        return redirect('/library/login')
        
    conn = get_lib_db(); c = conn.cursor()
    c.execute("UPDATE lib_users SET password=? WHERE UPPER(user_id)=?", (hash_pw(new_pw), uid))
    c.execute("SELECT name, phone FROM lib_users WHERE UPPER(user_id)=?", (uid,))
    user = c.fetchone()
    conn.commit()
    conn.close()
    
    if user and user['phone']:
        msg = f"*Contai Polytechnic Library* 📚\n\nHello {user['name']},\nYour password has been successfully reset! ✅\n\n_If you did not make this change, please contact the library admin immediately._"
        try:
            import urllib.request, json
            req = urllib.request.Request('http://localhost:3001/send', 
                data=json.dumps({'phone': user['phone'], 'message': msg}).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req) as response:
                pass
        except Exception as e:
            print("WhatsApp Bot Error:", e)
            
    session.pop('reset_otp', None)
    flash('✅ Password Reset Successful! Please login with your new password.', 'success')
    return redirect('/library/login')

@app.route('/library/logout')
def lib_logout():
    session.pop('lib_user', None)
    return redirect('/library/login')

@app.route('/library')
@login_required
def lib_home():
    conn = get_lib_db(); c = conn.cursor()
    c.execute("SELECT COUNT(*) as n FROM ebooks");                         tb = c.fetchone()['n']
    c.execute("SELECT COUNT(*) as n FROM lib_users WHERE role='student'"); ts = c.fetchone()['n']
    c.execute("SELECT SUM(downloads) FROM ebooks");                        dl = c.fetchone()[0] or 0
    c.execute("SELECT SUM(reads) FROM ebooks");                            rd = c.fetchone()[0] or 0
    c.execute("SELECT * FROM ebooks ORDER BY upload_date DESC LIMIT 8");   recent = c.fetchall()
    dept_counts = {}
    subj_counts = {}
    for d in get_departments():
        c.execute("SELECT COUNT(*) as n FROM ebooks WHERE dept=?", (d['id'],))
        dept_counts[d['id']] = c.fetchone()['n']
        if d.get('subjects'):
            for s in d['subjects']:
                c.execute("SELECT COUNT(*) as n FROM ebooks WHERE dept=? AND subject=?", (d['id'], s['name']))
                subj_counts[f"{d['id']}_{s['name']}"] = c.fetchone()['n']
    conn.close()
    return render_template('library/home.html', departments=get_departments(),
        recent=recent, dept_counts=dept_counts, subj_counts=subj_counts,
        stats={'books': tb, 'students': ts, 'downloads': dl, 'reads': rd},
        user=session['lib_user'])

@app.route('/library/dept/<dept_id>')
@login_required
def lib_dept(dept_id):
    dept = next((d for d in get_departments() if d['id'] == dept_id), None)
    if not dept:
        return redirect('/library')
    conn = get_lib_db(); c = conn.cursor()
    sem_f  = request.args.get('sem', '')
    subj_f = request.args.get('subject', '')
    sql = "SELECT * FROM ebooks WHERE dept=?"; params = [dept_id]
    if sem_f:  sql += " AND semester=?"; params.append(sem_f)
    if subj_f: sql += " AND subject=?"; params.append(subj_f)
    sql += " ORDER BY subject, title"
    c.execute(sql, params); books = c.fetchall()
    c.execute("SELECT DISTINCT subject FROM ebooks WHERE dept=? ORDER BY subject", (dept_id,))
    subjects = [r['subject'] for r in c.fetchall()]
    c.execute("SELECT DISTINCT semester FROM ebooks WHERE dept=? AND semester!='' ORDER BY semester", (dept_id,))
    semesters = [r['semester'] for r in c.fetchall()]
    c.execute("SELECT subject, COUNT(*) as c FROM ebooks WHERE dept=? GROUP BY subject", (dept_id,))
    subj_counts = {r['subject']: r['c'] for r in c.fetchall()}
    conn.close()
    return render_template('library/department.html', dept=dept, books=books,
        subjects=subjects, semesters=semesters, subj_counts=subj_counts,
        sem_filter=sem_f, subj_filter=subj_f, user=session['lib_user'])

@app.route('/library/read/<int:book_id>')
@login_required
def lib_read(book_id):
    conn = get_lib_db(); c = conn.cursor()
    c.execute("SELECT * FROM ebooks WHERE id=?", (book_id,)); book = c.fetchone()
    if not book: abort(404)
    c.execute("UPDATE ebooks SET reads=reads+1 WHERE id=?", (book_id,))
    c.execute("INSERT INTO read_history(user_id,book_id,action,date) VALUES(?,?,?,?)",
              (session['lib_user']['id'], book_id, 'read', lib_now()))
    conn.commit(); conn.close()
    depts = get_departments()
    dept = next((d for d in depts if d['id'] == book['dept']), depts[0] if depts else None)
    return render_template('library/reader.html', book=book, dept=dept, user=session['lib_user'])

@app.route('/library/file/<int:book_id>')
@login_required
def lib_serve_file(book_id):
    conn = get_lib_db(); c = conn.cursor()
    c.execute("SELECT filename, drive_link FROM ebooks WHERE id=?", (book_id,)); row = c.fetchone()
    conn.close()
    if not row: abort(404)
    # If it's a Drive-linked book, redirect to the Drive preview URL
    if row['drive_link']:
        return redirect(row['drive_link'])
    path = os.path.join(LIB_UPLOAD, row['filename'])
    if not os.path.exists(path): abort(404)
    ext = row['filename'].rsplit('.', 1)[-1].lower() if '.' in row['filename'] else 'pdf'
    mime_map = {
        'pdf': 'application/pdf', 'epub': 'application/epub+zip',
        'doc': 'application/msword',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'ppt': 'application/vnd.ms-powerpoint',
        'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    }
    return send_file(path, mimetype=mime_map.get(ext, 'application/octet-stream'))

@app.route('/library/download/<int:book_id>')
@login_required
def lib_download(book_id):
    if session['lib_user'].get('role') == 'student':
        flash('🚫 Students are not allowed to download. You can only read online.', 'error')
        return redirect('/library')
    conn = get_lib_db(); c = conn.cursor()
    c.execute("SELECT * FROM ebooks WHERE id=?", (book_id,)); book = c.fetchone()
    if not book: abort(404)
    c.execute("UPDATE ebooks SET downloads=downloads+1 WHERE id=?", (book_id,))
    c.execute("INSERT INTO read_history(user_id,book_id,action,date) VALUES(?,?,?,?)",
              (session['lib_user']['id'], book_id, 'download', lib_now()))
    conn.commit(); conn.close()
    # If Drive-linked, redirect to the Drive link
    if book['drive_link']:
        return redirect(book['drive_link'])
    path = os.path.join(LIB_UPLOAD, book['filename'])
    if not os.path.exists(path):
        flash('File পাওয়া যাচ্ছে না!', 'error'); return redirect('/library')
    return send_file(path, as_attachment=True, download_name=book['title'] + '.' + book['filetype'])

@app.route('/library/search')
@login_required
def lib_search():
    q       = request.args.get('q', '').strip()
    dept_f  = request.args.get('dept', '')
    subj_f  = request.args.get('subject', '')
    books   = []
    if q or dept_f or subj_f:
        conn = get_lib_db(); c = conn.cursor()
        sql = "SELECT * FROM ebooks WHERE 1=1"
        p   = []
        if q:
            sql += " AND (title LIKE ? OR author LIKE ? OR subject LIKE ? OR description LIKE ?)"
            p += [f'%{q}%'] * 4
        if dept_f:  sql += " AND dept=?";    p.append(dept_f)
        if subj_f:  sql += " AND subject=?"; p.append(subj_f)
        c.execute(sql + " ORDER BY title", p)
        books = c.fetchall(); conn.close()
    return render_template('library/search.html', books=books, q=q,
        dept_filter=dept_f, subj_filter=subj_f,
        departments=get_departments(), user=session['lib_user'])

@app.route('/library/profile')
@login_required
def lib_profile():
    conn = get_lib_db(); c = conn.cursor()
    c.execute("SELECT * FROM lib_users WHERE id=?", (session['lib_user']['id'],)); profile = c.fetchone()
    c.execute("""SELECT rh.action, rh.date, e.title, e.dept, e.subject, e.id as book_id
                 FROM read_history rh JOIN ebooks e ON rh.book_id=e.id
                 WHERE rh.user_id=? ORDER BY rh.date DESC LIMIT 20""",
              (session['lib_user']['id'],)); history = c.fetchall()
    conn.close()
    return render_template('library/profile.html', profile=profile, history=history,
        departments=get_departments(), user=session['lib_user'])

@app.route('/library/profile/change-password', methods=['POST'])
@login_required
def lib_change_password():
    old = request.form['old_password']; new = request.form['new_password']
    conn = get_lib_db(); c = conn.cursor()
    c.execute("SELECT password FROM lib_users WHERE id=?", (session['lib_user']['id'],)); row = c.fetchone()
    if row['password'] != hash_pw(old): flash('Old Password is incorrect!', 'error')
    elif len(new) < 6:                  flash('New Password must be at least 6 characters!', 'error')
    else:
        c.execute("UPDATE lib_users SET password=? WHERE id=?", (hash_pw(new), session['lib_user']['id']))
        conn.commit(); flash('✅ Password changed successfully!', 'success')
    conn.close(); return redirect('/library/profile')

# ── Library Admin ──
@app.route('/library/admin')
@admin_required
def lib_admin():
    conn = get_lib_db(); c = conn.cursor()
    c.execute("SELECT COUNT(*) as n FROM ebooks");                         tb  = c.fetchone()['n']
    c.execute("SELECT COUNT(*) as n FROM lib_users WHERE role='student'"); ts  = c.fetchone()['n']
    c.execute("SELECT COUNT(*) as n FROM lib_users WHERE role='teacher'"); tt  = c.fetchone()['n']
    c.execute("SELECT COUNT(*) as n FROM lib_users WHERE status='pending'"); tp  = c.fetchone()['n']
    c.execute("SELECT SUM(downloads) FROM ebooks");                        tdl = c.fetchone()[0] or 0
    c.execute("SELECT SUM(reads) FROM ebooks");                            trd = c.fetchone()[0] or 0
    c.execute("SELECT * FROM ebooks ORDER BY upload_date DESC LIMIT 5");   rb  = c.fetchall()
    c.execute("SELECT * FROM lib_users WHERE status='active' ORDER BY created DESC LIMIT 5"); ru = c.fetchall()
    c.execute("SELECT * FROM read_history ORDER BY date DESC LIMIT 10");   ra  = c.fetchall()
    dept_stats = []
    for d in get_departments():
        c.execute("SELECT COUNT(*) as n FROM ebooks WHERE dept=?", (d['id'],))
        dept_stats.append({'dept': d, 'count': c.fetchone()['n']})
    # Get pending users for approval section
    c.execute("SELECT * FROM lib_users WHERE status='pending' ORDER BY created DESC"); pending_users = c.fetchall()
    conn.close()
    return render_template('library/admin_home.html',
        departments=get_departments(), recent_books=rb, recent_users=ru, recent_activity=ra,
        dept_stats=dept_stats, user=session['lib_user'], pending_users=pending_users,
        stats={'books': tb, 'students': ts, 'teachers': tt, 'downloads': tdl, 'reads': trd, 'pending': tp})

@app.route('/library/admin/books')
@admin_required
def lib_admin_books():
    dept_f = request.args.get('dept', '')
    conn = get_lib_db(); c = conn.cursor()
    if dept_f: c.execute("SELECT * FROM ebooks WHERE dept=? ORDER BY upload_date DESC", (dept_f,))
    else:       c.execute("SELECT * FROM ebooks ORDER BY upload_date DESC")
    books = c.fetchall(); conn.close()
    return render_template('library/admin_books.html', books=books,
        departments=get_departments(), dept_filter=dept_f, user=session['lib_user'])

@app.route('/library/admin/upload', methods=['POST'])
@admin_required
def lib_upload():
    drive_link = request.form.get('drive_link', '').strip()
    f = request.files.get('ebook_file')
    
    if drive_link:
        # ── Google Drive link upload ──
        # Extract file ID from various Drive URL formats
        import re
        file_id = ''
        m = re.search(r'/d/([a-zA-Z0-9_-]+)', drive_link)
        if m:
            file_id = m.group(1)
        elif 'id=' in drive_link:
            m2 = re.search(r'id=([a-zA-Z0-9_-]+)', drive_link)
            if m2: file_id = m2.group(1)
        
        if not file_id:
            flash('Invalid Google Drive link! Please use a valid sharing URL.', 'error')
            return redirect('/library/admin/books')
        
        # Build the preview/embed URL
        preview_url = f'https://drive.google.com/file/d/{file_id}/preview'
        
        conn = get_lib_db(); c = conn.cursor()
        c.execute("""INSERT INTO ebooks(title,author,description,dept,subject,semester,
                     filename,uploaded_by,upload_date,filesize,filetype,drive_link,book_subject) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (request.form['title'], request.form.get('author', ''),
                   request.form.get('description', ''), request.form['dept'],
                   request.form['subject'], request.form.get('semester', ''),
                   'drive_' + file_id, session['lib_user']['name'], lib_now(), 'Drive Link', 'pdf',
                   preview_url, request.form.get('book_subject', '')))
        conn.commit(); conn.close()
        flash(f'✅ "{request.form["title"]}" (Drive Link) added successfully!', 'success')
        return redirect('/library/admin/books')
    
    # ── Normal file upload ──
    if not f or not f.filename or not lib_allowed_file(f.filename):
        flash('Please provide a valid file (PDF, EPUB, DOC, DOCX, PPT, PPTX) or a Google Drive link.', 'error')
        return redirect('/library/admin/books')
    ext = f.filename.rsplit('.', 1)[1].lower()
    ts = datetime.now().strftime('%Y%m%d%H%M%S')
    filename = ts + '_' + secure_filename(f.filename)
    os.makedirs(LIB_UPLOAD, exist_ok=True)
    filepath = os.path.join(LIB_UPLOAD, filename)
    f.save(filepath)
    size = os.path.getsize(filepath)
    size_str = f"{size/1024:.1f} KB" if size < 1048576 else f"{size/1048576:.1f} MB"
    conn = get_lib_db(); c = conn.cursor()
    c.execute("""INSERT INTO ebooks(title,author,description,dept,subject,semester,
                 filename,uploaded_by,upload_date,filesize,filetype,drive_link,book_subject) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (request.form['title'], request.form.get('author', ''),
               request.form.get('description', ''), request.form['dept'],
               request.form['subject'], request.form.get('semester', ''),
               filename, session['lib_user']['name'], lib_now(), size_str, ext, '', request.form.get('book_subject', '')))
    conn.commit(); conn.close()
    flash(f'✅ "{request.form["title"]}" uploaded successfully!', 'success')
    return redirect('/library/admin/books')

@app.route('/library/admin/delete-book/<int:bid>', methods=['POST'])
@admin_required
def lib_delete_book(bid):
    conn = get_lib_db(); c = conn.cursor()
    c.execute("SELECT filename FROM ebooks WHERE id=?", (bid,)); row = c.fetchone()
    if row:
        fp = os.path.join(LIB_UPLOAD, row['filename'])
        if os.path.exists(fp): os.remove(fp)
        c.execute("DELETE FROM ebooks WHERE id=?", (bid,))
        conn.commit(); flash('Book deleted successfully.', 'success')
    conn.close(); return redirect('/library/admin/books')

@app.route('/library/admin/users')
@admin_required
def lib_admin_users():
    role_f = request.args.get('role', ''); dept_f = request.args.get('dept', ''); status_f = request.args.get('status', ''); q = request.args.get('q', '').strip()
    conn = get_lib_db(); c = conn.cursor()
    sql = "SELECT * FROM lib_users WHERE 1=1"; params = []
    if role_f: sql += " AND role=?"; params.append(role_f)
    if dept_f: sql += " AND dept=?"; params.append(dept_f)
    if status_f: sql += " AND status=?"; params.append(status_f)
    if q:
        sql += " AND (name LIKE ? OR user_id LIKE ? OR reg_no LIKE ? OR phone LIKE ?)"
        q_param = f"%{q}%"
        params.extend([q_param, q_param, q_param, q_param])
    c.execute(sql + " ORDER BY created DESC", params); users = c.fetchall(); conn.close()
    return render_template('library/admin_users.html', users=users,
        departments=get_departments(), role_filter=role_f, dept_filter=dept_f, status_filter=status_f, search_q=q, user=session['lib_user'])

@app.route('/library/admin/add-user', methods=['POST'])
@admin_required
def lib_add_user():
    reg_no = request.form.get('reg_no', '').strip().upper()
    name = request.form['name'].strip()
    role = request.form.get('role', 'student')
    dept = request.form.get('dept', '')
    email= request.form.get('email', '')
    phone= request.form.get('phone', '').strip()
    
    # Generate random 6-digit password
    import random
    pw = str(random.randint(100000, 999999))
    
    # Generate Library ID: LIBCON + last 4 digits of reg_no + 2 random digits
    suffix = reg_no[-4:] if len(reg_no) >= 4 else reg_no
    uid = f"LIBCON{suffix}{random.randint(10, 99)}"
    
    conn = get_lib_db(); c = conn.cursor()
    # Ensure unique Library ID
    while True:
        c.execute("SELECT id FROM lib_users WHERE UPPER(user_id)=?", (uid.upper(),))
        if not c.fetchone():
            break
        uid = f"LIBCON{suffix}{random.randint(10, 99)}"
    try:
        c.execute("INSERT INTO lib_users(name,user_id,reg_no,password,role,dept,email,phone,status,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (name, uid, reg_no, hash_pw(pw), role, dept, email, phone, 'active', lib_today()))
        conn.commit()
        
        # Send WhatsApp message
        if phone:
            msg = f"*Welcome to Contai Polytechnic Library!* 📚\n\nHello {name},\nAn account has been created for you by the Admin.\n\n*Library ID:* {uid}\n*Registration No:* {reg_no}\n*Password:* {pw}\n\n_Please login using either ID to access the digital library. We highly recommend changing your password after your first login._"
            try:
                import urllib.request, json
                req = urllib.request.Request('http://localhost:3001/send', 
                    data=json.dumps({'phone': phone, 'message': msg}).encode('utf-8'),
                    headers={'Content-Type': 'application/json'}
                )
                with urllib.request.urlopen(req) as response:
                    pass
            except Exception as e:
                print("WhatsApp Bot Error on Add User:", e)
                
        flash(f'✅ "{name}" (ID:{uid}) added successfully!', 'success')
    except sqlite3.IntegrityError:
        flash(f'ID "{uid}" already exists!', 'error')
    finally:
        conn.close()
    return redirect('/library/admin/users')

@app.route('/library/admin/toggle-user/<int:uid>', methods=['POST'])
@admin_required
def lib_toggle_user(uid):
    conn = get_lib_db(); c = conn.cursor()
    c.execute("SELECT * FROM lib_users WHERE id=?", (uid,)); row = c.fetchone()
    if row and row['user_id'] != 'LIB-ADMIN':
        # pending → active (approve), active → inactive, inactive → active
        if row['status'] == 'active':
            new_s = 'inactive'
        else:
            new_s = 'active'
        c.execute("UPDATE lib_users SET status=? WHERE id=?", (new_s, uid))
        conn.commit()
        
        # Send WhatsApp notification when approving (pending/inactive → active)
        if new_s == 'active' and row['phone']:
            msg = f"*Contai Polytechnic Library* 📚\n\nHello {row['name']},\nYour library account has been *approved* by the Admin! ✅\n\n*Library ID:* {row['user_id']}\n*Reg. No:* {row['reg_no']}\n\n_You can now login and access the digital library._"
            try:
                import urllib.request as _ur, json as _js
                req = _ur.Request('http://localhost:3001/send', 
                    data=_js.dumps({'phone': row['phone'], 'message': msg}).encode('utf-8'),
                    headers={'Content-Type': 'application/json'}
                )
                with _ur.urlopen(req) as response:
                    pass
            except Exception as e:
                print("WhatsApp Bot Error on Approval:", e)
        
        status_label = 'Approved ✅' if row['status'] in ('pending',) and new_s == 'active' else new_s
        flash(f'User status → {status_label}', 'success')
    conn.close(); return redirect(request.referrer or '/library/admin/users')

@app.route('/library/admin/delete-user/<int:uid>', methods=['POST'])
@only_lib_admin
def lib_delete_user(uid):
    conn = get_lib_db(); c = conn.cursor()
    c.execute("SELECT user_id FROM lib_users WHERE id=?", (uid,)); row = c.fetchone()
    if row and row['user_id'] != 'LIB-ADMIN':
        c.execute("DELETE FROM lib_users WHERE id=?", (uid,))
        conn.commit(); flash('User removed successfully.', 'success')
    conn.close(); return redirect('/library/admin/users')

@app.route('/library/admin/reset-password/<int:uid>', methods=['POST'])
@admin_required
def lib_reset_password(uid):
    new_pw = request.form.get('new_password', 'pass1234')
    conn = get_lib_db(); c = conn.cursor()
    c.execute("SELECT name, phone FROM lib_users WHERE id=?", (uid,))
    user = c.fetchone()
    
    if user:
        c.execute("UPDATE lib_users SET password=? WHERE id=?", (hash_pw(new_pw), uid))
        conn.commit()
        
        # Send WhatsApp Message if phone exists
        if user['phone']:
            msg = f"*Contai Polytechnic Library* 📚\n\nHello {user['name']},\nYour account password has been reset by the Admin.\n\n*New Password:* {new_pw}\n\n_Please login and change your password._"
            try:
                import urllib.request, json
                req = urllib.request.Request('http://localhost:3001/send', 
                    data=json.dumps({'phone': user['phone'], 'message': msg}).encode('utf-8'),
                    headers={'Content-Type': 'application/json'}
                )
                with urllib.request.urlopen(req) as response:
                    pass
            except Exception as e:
                print("WhatsApp Bot Error:", e)
                
        flash(f'Password reset → {new_pw}', 'success')
    conn.close()
    return redirect('/library/admin/users')

@app.route('/library/admin/edit-user/<int:uid>', methods=['POST'])
@admin_required
def lib_edit_user(uid):
    name = request.form.get('name', '').strip()
    user_id = request.form.get('user_id', '').strip().upper()
    role = request.form.get('role', 'student')
    dept = request.form.get('dept', '')
    phone = request.form.get('phone', '').strip()
    email = request.form.get('email', '').strip()
    
    conn = get_lib_db(); c = conn.cursor()
    try:
        # Check if user_id is already used by someone else
        c.execute("SELECT id FROM lib_users WHERE UPPER(user_id)=? AND id!=?", (user_id, uid))
        if c.fetchone():
            flash(f'Library ID "{user_id}" already exists!', 'error')
        else:
            c.execute("""UPDATE lib_users SET name=?, user_id=?, role=?, dept=?, phone=?, email=? WHERE id=?""",
                      (name, user_id, role, dept, phone, email, uid))
            conn.commit()
            flash('User updated successfully.', 'success')
    except Exception as e:
        flash('Error updating user.', 'error')
    finally:
        conn.close()
    return redirect('/library/admin/users')

@app.route('/library/admin/categories')
@admin_required
def lib_admin_categories():
    return render_template('library/admin_categories.html', departments=get_departments(), user=session['lib_user'])

@app.route('/library/admin/whatsapp')
@admin_required
def lib_admin_whatsapp():
    return render_template('library/admin_whatsapp.html', user=session['lib_user'])

@app.route('/api/wa-hard-reset', methods=['POST'])
def wa_hard_reset():
    """Hard reset: kill node bot process, clear wa_session, and restart."""
    import shutil, glob
    try:
        # Step 1: Kill any node process using port 3001
        result = subprocess.run(
            ['powershell', '-Command',
             "Get-NetTCPConnection -LocalPort 3001 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess"],
            capture_output=True, text=True, timeout=5
        )
        pids = set(result.stdout.strip().split('\n')) if result.stdout.strip() else set()
        killed = []
        for pid in pids:
            pid = pid.strip()
            if pid and pid.isdigit() and int(pid) > 0:
                try:
                    subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True, timeout=5)
                    killed.append(pid)
                except Exception:
                    pass
        _time.sleep(2)

        # Step 2: Delete wa_session folder completely
        session_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wa_bot', 'wa_session')
        if os.path.exists(session_dir):
            shutil.rmtree(session_dir, ignore_errors=True)
            print(f"Hard Reset: Deleted wa_session folder.")

        # Step 3: Start bot fresh
        bot_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wa_bot')
        log_file = os.path.join(bot_dir, 'bot.log')
        log_f = open(log_file, 'w', encoding='utf-8')
        kwargs = {}
        if sys.platform == 'win32':
            kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            kwargs['start_new_session'] = True
        proc = subprocess.Popen(
            ['node', 'server.js'],
            cwd=bot_dir,
            stdout=log_f,
            stderr=log_f,
            **kwargs
        )
        print(f"Hard Reset: Bot restarted (PID {proc.pid}).")
        return jsonify({'success': True, 'message': f'Bot hard reset done. Killed PIDs: {killed}. Restarting fresh...'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/library/admin/add-category', methods=['POST'])
@admin_required
def lib_add_category():
    cid = request.form['id'].strip().lower()
    name = request.form['name'].strip()
    icon = request.form.get('icon', '📁').strip()
    color = request.form.get('color', '#3b82f6').strip()
    heading = request.form.get('heading', 'DEPARTMENTS').strip().upper()
    conn = get_lib_db(); c = conn.cursor()
    try:
        c.execute("INSERT INTO lib_departments (id, name, icon, color, heading) VALUES (?,?,?,?,?)", (cid, name, icon, color, heading))
        conn.commit(); flash(f'✅ Category "{name}" added!', 'success')
    except sqlite3.IntegrityError:
        flash(f'Category ID "{cid}" already exists!', 'error')
    conn.close(); return redirect('/library/admin/categories')

@app.route('/library/admin/delete-category/<cid>', methods=['POST'])
@admin_required
def lib_delete_category(cid):
    conn = get_lib_db(); c = conn.cursor()
    c.execute("DELETE FROM lib_departments WHERE id=?", (cid,))
    conn.commit(); flash('Category removed!', 'success')
    conn.close(); return redirect('/library/admin/categories')

@app.route('/library/admin/add-subject', methods=['POST'])
@admin_required
def lib_add_subject():
    dept_id = request.form['dept_id'].strip()
    name = request.form['name'].strip()
    icon = request.form.get('icon', '📄').strip()
    color = request.form.get('color', '#64748b').strip()
    conn = get_lib_db(); c = conn.cursor()
    try:
        c.execute("INSERT INTO lib_subjects (dept_id, name, icon, color) VALUES (?,?,?,?)", (dept_id, name, icon, color))
        conn.commit(); flash(f'✅ Subject "{name}" added!', 'success')
    except sqlite3.IntegrityError:
        flash(f'Subject "{name}" already exists in this category!', 'error')
    conn.close(); return redirect('/library/admin/categories')

@app.route('/library/admin/delete-subject/<int:sid>', methods=['POST'])
@admin_required
def lib_delete_subject(sid):
    conn = get_lib_db(); c = conn.cursor()
    c.execute("DELETE FROM lib_subjects WHERE id=?", (sid,))
    conn.commit(); flash('Subject removed!', 'success')
    conn.close(); return redirect('/library/admin/categories')

# ── Shortcut redirects (Enter Library / Login buttons) ──
@app.route('/go_library')
def go_library():
    return redirect('/library')

@app.route('/go_library/login')
def go_library_login():
    return redirect('/library/login')

@app.route('/go_library/dept/<dept_id>')
def go_library_dept(dept_id):
    return redirect(f'/library/dept/{dept_id}')

# ════════════════════════════════════════════════════════════


# ---- NOTICE BOARD ----

def init_notices_table():
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT,
                category TEXT DEFAULT 'General',
                department TEXT DEFAULT 'General',
                date TEXT,
                link TEXT
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Notice Table Error: {e}")

def get_all_notices():
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, body, category, department, date, link FROM notices ORDER BY id DESC")
        data = cursor.fetchall()
        conn.close()
        return data
    except Exception as e:
        print(f"Notice Fetch Error: {e}")
        return []

@app.route("/notice")
def notice_board():
    notices = get_all_notices()
    return render_template("notice_board.html", notices=notices)

@app.route("/admin/notice/add", methods=["POST"])
def add_notice():
    if "admin" not in session:
        return redirect("/login")
    title    = request.form.get("title", "").strip()
    body     = request.form.get("body", "").strip()
    category = request.form.get("category", "General").strip()
    dept     = request.form.get("department", "General").strip()
    dt       = request.form.get("date", "").strip()
    link     = request.form.get("link", "").strip()
    if title:
        try:
            conn = sqlite3.connect("database.db")
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO notices (title, body, category, department, date, link) VALUES (?,?,?,?,?,?)",
                (title, body, category, dept, dt, link)
            )
            conn.commit()
            conn.close()
            flash("✅ Notice posted successfully!", "success")
        except Exception as e:
            print(f"Add Notice Error: {e}")
            flash("❌ Failed to post notice.", "error")
    else:
        flash("⚠️ Title is required!", "error")
    back = "/admin#notices" if request.form.get('redirect_to') == 'admin' else "/admin/notices"
    return redirect(back)

@app.route("/admin/notice/delete/<int:notice_id>")
def delete_notice(notice_id):
    if "admin" not in session:
        return redirect("/login")
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM notices WHERE id=?", (notice_id,))
        conn.commit()
        conn.close()
        flash("🗑️ Notice deleted!", "success")
    except Exception as e:
        print(f"Delete Notice Error: {e}")
        flash("❌ Failed to delete notice.", "error")
    back = "/admin#notices" if request.args.get('from') == 'admin' else "/admin/notices"
    return redirect(back)

@app.route("/admin/notices")
def admin_notices():
    if "admin" not in session:
        return redirect("/login")
    notices = get_all_notices()
    return render_template("admin_notices.html", notices=notices, now=date.today().isoformat())

@app.route("/delete/<int:qa_id>")
def delete_qa(qa_id):
    if "admin" not in session:
        return redirect("/login")

    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM qa WHERE id=?", (qa_id,))
        conn.commit()
        conn.close()
        load_model()
        flash("🗑️ Q&A deleted!", "success")
    except Exception as e:
        print(f"Delete Error: {e}")
        flash("❌ Failed to delete. Try again.", "error")

    return redirect("/view_all")


@app.route("/qa/add", methods=["POST"])
def qa_add_api():
    """JSON API: Add a Q&A entry with duplicate prevention."""
    if "admin" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    data = request.get_json(silent=True) or {}
    q = (data.get("question") or request.form.get("question") or "").strip()
    a = (data.get("answer")   or request.form.get("answer")   or "").strip()

    if not q or not a:
        return jsonify({"success": False, "error": "Both question and answer are required!"})

    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        # Duplicate check (case-insensitive)
        cursor.execute("SELECT id FROM qa WHERE LOWER(TRIM(question))=LOWER(TRIM(?))", (q,))
        existing = cursor.fetchone()
        if existing:
            conn.close()
            return jsonify({"success": False, "error": "This question already exists in the database!", "duplicate": True})
        cursor.execute("INSERT INTO qa (question, answer, source) VALUES (?,?,?)", (q, a, 'manual'))
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()
        load_model()
        return jsonify({"success": True, "id": new_id, "message": "Q&A added successfully!"})
    except Exception as e:
        print(f"QA Add Error: {e}")
        return jsonify({"success": False, "error": "Database error. Try again."})


@app.route("/qa/edit/<int:qa_id>", methods=["POST"])
def qa_edit_api(qa_id):
    """JSON API: Edit an existing Q&A entry."""
    if "admin" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    data = request.get_json(silent=True) or {}
    q = (data.get("question") or "").strip()
    a = (data.get("answer")   or "").strip()

    if not q or not a:
        return jsonify({"success": False, "error": "Both question and answer are required!"})

    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        # Duplicate check — exclude current row
        cursor.execute("SELECT id FROM qa WHERE LOWER(TRIM(question))=LOWER(TRIM(?)) AND id!=?", (q, qa_id))
        if cursor.fetchone():
            conn.close()
            return jsonify({"success": False, "error": "Another entry with this question already exists!", "duplicate": True})
        cursor.execute("UPDATE qa SET question=?, answer=? WHERE id=?", (q, a, qa_id))
        conn.commit()
        conn.close()
        load_model()
        return jsonify({"success": True, "message": "Q&A updated successfully!"})
    except Exception as e:
        print(f"QA Edit Error: {e}")
        return jsonify({"success": False, "error": "Database error. Try again."})


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ---- HOME PAGE CONTENT (Events / Faculty / Placement) ----

def init_homepage_tables():
    try:
        conn = sqlite3.connect("database.db")
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS hp_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                month TEXT NOT NULL,
                tag TEXT DEFAULT 'General',
                tag_color TEXT DEFAULT 'exam',
                title TEXT NOT NULL,
                description TEXT,
                location TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS hp_faculty (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                photo TEXT,
                initials TEXT,
                name TEXT NOT NULL,
                department TEXT,
                designation TEXT,
                qualification TEXT,
                years_exp TEXT,
                papers TEXT DEFAULT '0',
                students TEXT DEFAULT '0'
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS hp_placement (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS hp_companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                emoji TEXT DEFAULT '🔵'
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS hp_alumni (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                initials TEXT NOT NULL,
                name TEXT NOT NULL,
                quote TEXT,
                role TEXT,
                batch TEXT
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Homepage Tables Error: {e}")

def get_hp_events():
    try:
        conn = sqlite3.connect("database.db")
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM hp_events ORDER BY id DESC")
        data = c.fetchall()
        conn.close()
        return data
    except: return []

def get_hp_faculty():
    try:
        conn = sqlite3.connect("database.db")
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM hp_faculty ORDER BY id ASC")
        data = c.fetchall()
        conn.close()
        return data
    except: return []

def get_hp_placement_stats():
    try:
        conn = sqlite3.connect("database.db")
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT key, value FROM hp_placement")
        rows = c.fetchall()
        conn.close()
        return {r['key']: r['value'] for r in rows}
    except: return {}

def get_hp_companies():
    try:
        conn = sqlite3.connect("database.db")
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM hp_companies ORDER BY id ASC")
        data = c.fetchall()
        conn.close()
        return data
    except: return []

def get_hp_alumni():
    try:
        conn = sqlite3.connect("database.db")
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM hp_alumni ORDER BY id DESC")
        data = c.fetchall()
        conn.close()
        return data
    except: return []

# ── Events CRUD ──
@app.route("/admin/hp/event/add", methods=["POST"])
def hp_add_event():
    if "admin" not in session: return redirect("/login")
    day = request.form.get("day","").strip()
    month = request.form.get("month","").strip()
    tag = request.form.get("tag","General").strip()
    tag_color = request.form.get("tag_color","exam").strip()
    title = request.form.get("title","").strip()
    desc = request.form.get("description","").strip()
    loc = request.form.get("location","").strip()
    if title and day and month:
        conn = sqlite3.connect("database.db"); c = conn.cursor()
        c.execute("INSERT INTO hp_events (day,month,tag,tag_color,title,description,location) VALUES (?,?,?,?,?,?,?)",
                  (day,month,tag,tag_color,title,desc,loc))
        conn.commit(); conn.close()
        flash("✅ Event added!", "success")
    else:
        flash("⚠️ Title, Day & Month required!", "error")
    return redirect("/admin#homepage")

@app.route("/admin/hp/event/delete/<int:eid>")
def hp_delete_event(eid):
    if "admin" not in session: return redirect("/login")
    conn = sqlite3.connect("database.db"); c = conn.cursor()
    c.execute("DELETE FROM hp_events WHERE id=?", (eid,))
    conn.commit(); conn.close()
    flash("🗑️ Event deleted!", "success")
    return redirect("/admin#homepage")

# ── Faculty CRUD ──
@app.route("/admin/hp/faculty/add", methods=["POST"])
def hp_add_faculty():
    if "admin" not in session: return redirect("/login")
    name = request.form.get("name","").strip()
    file = request.files.get("photo")
    dept = request.form.get("department","").strip()
    desig = request.form.get("designation","").strip()
    qual = request.form.get("qualification","").strip()
    yrs = request.form.get("years_exp","").strip()
    
    if name and file and file.filename != "":
        if not allowed_file(file.filename):
            flash("❌ Invalid file type! Only JPG, PNG, WEBP allowed.", "error")
            return redirect("/admin#homepage")
            
        os.makedirs(os.path.join('static', 'faculty'), exist_ok=True)
        ts       = datetime.now().strftime("%Y%m%d%H%M%S_")
        filename = ts + secure_filename(file.filename)
        file.save(os.path.join('static', 'faculty', filename))
        
        conn = sqlite3.connect("database.db"); c = conn.cursor()
        c.execute("INSERT INTO hp_faculty (photo,initials,name,department,designation,qualification,years_exp) VALUES (?,?,?,?,?,?,?)",
                  (filename,"",name,dept,desig,qual,yrs))
        conn.commit(); conn.close()
        flash("✅ Faculty added!", "success")
    else:
        flash("⚠️ Name & Photo required!", "error")
    return redirect("/admin#homepage")

@app.route("/admin/hp/faculty/delete/<int:fid>")
def hp_delete_faculty(fid):
    if "admin" not in session: return redirect("/login")
    conn = sqlite3.connect("database.db"); c = conn.cursor()
    c.execute("SELECT photo FROM hp_faculty WHERE id=?", (fid,))
    row = c.fetchone()
    if row and row[0]:
        filepath = os.path.join('static', 'faculty', row[0])
        if os.path.exists(filepath):
            os.remove(filepath)
    c.execute("DELETE FROM hp_faculty WHERE id=?", (fid,))
    conn.commit(); conn.close()
    flash("🗑️ Faculty removed!", "success")
    return redirect("/admin#homepage")

@app.route("/admin/hp/faculty/edit/<int:fid>", methods=["POST"])
def hp_edit_faculty(fid):
    if "admin" not in session: return redirect("/login")
    name = request.form.get("name","").strip()
    file = request.files.get("photo")
    dept = request.form.get("department","").strip()
    desig = request.form.get("designation","").strip()
    qual = request.form.get("qualification","").strip()
    yrs = request.form.get("years_exp","").strip()
    
    if name:
        conn = sqlite3.connect("database.db"); c = conn.cursor()
        if file and file.filename != "":
            if not allowed_file(file.filename):
                flash("❌ Invalid file type! Only JPG, PNG, WEBP allowed.", "error")
                return redirect("/admin#homepage")
            
            os.makedirs(os.path.join('static', 'faculty'), exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d%H%M%S_")
            filename = ts + secure_filename(file.filename)
            file.save(os.path.join('static', 'faculty', filename))
            
            # Delete old photo
            c.execute("SELECT photo FROM hp_faculty WHERE id=?", (fid,))
            row = c.fetchone()
            if row and row[0]:
                filepath = os.path.join('static', 'faculty', row[0])
                if os.path.exists(filepath):
                    try: os.remove(filepath)
                    except: pass
                    
            c.execute("UPDATE hp_faculty SET photo=?, name=?, department=?, designation=?, qualification=?, years_exp=? WHERE id=?",
                      (filename, name, dept, desig, qual, yrs, fid))
        else:
            c.execute("UPDATE hp_faculty SET name=?, department=?, designation=?, qualification=?, years_exp=? WHERE id=?",
                      (name, dept, desig, qual, yrs, fid))
        
        conn.commit(); conn.close()
        flash("✅ Faculty updated!", "success")
    else:
        flash("⚠️ Name is required!", "error")
    return redirect("/admin#homepage")

# ── Placement Stats ──
@app.route("/admin/hp/placement/save", methods=["POST"])
def hp_save_placement():
    if "admin" not in session: return redirect("/login")
    conn = sqlite3.connect("database.db"); c = conn.cursor()
    for key in ["placement_rate","avg_package","companies_count","alumni_count"]:
        val = request.form.get(key,"").strip()
        if val:
            c.execute("INSERT OR REPLACE INTO hp_placement (key, value) VALUES (?,?)", (key, val))
    conn.commit(); conn.close()
    flash("✅ Placement stats saved!", "success")
    return redirect("/admin#homepage")

# ── Companies CRUD ──
@app.route("/admin/hp/company/add", methods=["POST"])
def hp_add_company():
    if "admin" not in session: return redirect("/login")
    name = request.form.get("name","").strip()
    file = request.files.get("logo")
    logo_filename = None

    if file and file.filename != "" and allowed_file(file.filename):
        os.makedirs(os.path.join('static', 'company_logos'), exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d%H%M%S_")
        logo_filename = ts + secure_filename(file.filename)
        file.save(os.path.join('static', 'company_logos', logo_filename))

    if name:
        conn = sqlite3.connect("database.db"); c = conn.cursor()
        c.execute("INSERT INTO hp_companies (name, emoji, logo) VALUES (?,?,?)", (name, "🏭", logo_filename))
        conn.commit(); conn.close()
        flash("✅ Company added!", "success")
    return redirect("/admin#homepage")

@app.route("/admin/hp/company/delete/<int:cid>")
def hp_delete_company(cid):
    if "admin" not in session: return redirect("/login")
    conn = sqlite3.connect("database.db"); c = conn.cursor()
    c.execute("SELECT logo FROM hp_companies WHERE id=?", (cid,))
    row = c.fetchone()
    if row and row[0]:
        fpath = os.path.join('static', 'company_logos', row[0])
        if os.path.exists(fpath):
            try: os.remove(fpath)
            except: pass
    c.execute("DELETE FROM hp_companies WHERE id=?", (cid,))
    conn.commit(); conn.close()
    return redirect("/admin#homepage")

# ── Alumni CRUD ──
@app.route("/admin/hp/alumni/add", methods=["POST"])
def hp_add_alumni():
    if "admin" not in session: return redirect("/login")
    name = request.form.get("name","").strip()
    initials = request.form.get("initials","").strip().upper()
    quote = request.form.get("quote","").strip()
    role = request.form.get("role","").strip()
    batch = request.form.get("batch","").strip()
    if name and initials:
        conn = sqlite3.connect("database.db"); c = conn.cursor()
        c.execute("INSERT INTO hp_alumni (initials,name,quote,role,batch) VALUES (?,?,?,?,?)",
                  (initials,name,quote,role,batch))
        conn.commit(); conn.close()
        flash("✅ Alumni story added!", "success")
    return redirect("/admin#homepage")

@app.route("/admin/hp/alumni/delete/<int:aid>")
def hp_delete_alumni(aid):
    if "admin" not in session: return redirect("/login")
    conn = sqlite3.connect("database.db"); c = conn.cursor()
    c.execute("DELETE FROM hp_alumni WHERE id=?", (aid,))
    conn.commit(); conn.close()
    return redirect("/admin#homepage")

def init_gallery_table():
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS gallery (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                title    TEXT NOT NULL,
                filename TEXT NOT NULL,
                category TEXT DEFAULT 'campus'
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Gallery Table Error: {e}")

def get_all_gallery():
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, filename, category FROM gallery ORDER BY id DESC")
        data = cursor.fetchall()
        conn.close()
        return data
    except Exception as e:
        print(f"Gallery Fetch Error: {e}")
        return []

class GalleryItem:
    def __init__(self, id, title, filename, category):
        self.id = id
        self.caption = title
        self.filename = filename
        self.category = category

def get_gallery_for_admin():
    rows = get_all_gallery()
    return [GalleryItem(*r) for r in rows]

@app.route("/events")
def view_events():
    hp_events = get_hp_events()
    return render_template("events.html", hp_events=hp_events)

@app.route("/faculty")
def view_faculty():
    hp_faculty = get_hp_faculty()
    return render_template("faculty.html", hp_faculty=hp_faculty)

@app.route("/result", methods=["GET", "POST"])
def search_result():
    if request.method == "POST":
        reg_no = request.form.get("reg_no", "").strip()

        conn = sqlite3.connect("database.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        student = None
        if reg_no:
            # Search by Registration No
            cursor.execute("SELECT * FROM student_results WHERE reg_no = ?", (reg_no,))
            student = cursor.fetchone()
        
        if not student:
            conn.close()
            flash("❌ No result found with provided details. Please check and try again.", "error")
            return render_template("search_result.html")
            
        # Get marks
        cursor.execute("SELECT * FROM result_marks WHERE result_id = ?", (student['id'],))
        marks = cursor.fetchall()
        conn.close()
        
        # Calculations
        total_obtained = sum(m['marks'] for m in marks)
        total_full = sum(m['full_marks'] for m in marks)
        percentage = (total_obtained / total_full * 100) if total_full > 0 else 0
        sgpa = percentage / 10
        
        return render_template("view_result.html", student=student, marks=marks, 
                               total=total_obtained, full_total=total_full, 
                               percentage=round(percentage, 2), sgpa=round(sgpa, 2))
                               
    return render_template("search_result.html")

@app.route("/admin/result/add", methods=["POST"])
def admin_add_result():
    # Allow both main admin session AND library teacher/admin session
    is_main_admin = "admin" in session
    is_lib_teacher = (
        "lib_user" in session and
        session["lib_user"].get("role") in ("admin", "teacher")
    )
    if not is_main_admin and not is_lib_teacher:
        return redirect("/login")
        
    name = request.form.get("student_name", "").strip()
    reg  = request.form.get("reg_no", "").strip()
    dept = request.form.get("department", "").strip()
    sem  = request.form.get("semester", "").strip()
    exam = request.form.get("exam_name", "").strip()
    
    if not name or not reg or not dept or not sem or not exam:
        flash("⚠️ সব required field পূরণ করুন!", "error")
        return redirect("/admin#result")
    
    subjects = request.form.getlist("subject[]")
    marks_list = request.form.getlist("marks[]")
    full_marks_list = request.form.getlist("full_marks[]")
    
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM student_results WHERE reg_no = ?", (reg,))
        cursor.execute("""
            INSERT INTO student_results (reg_no, student_name, department, semester, exam_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (reg, name, dept, sem, exam, datetime.now().strftime("%Y-%m-%d %H:%M")))
        
        result_id = cursor.lastrowid
        inserted = 0
        
        for s, m, f in zip(subjects, marks_list, full_marks_list):
            if s and m:
                try:
                    marks_val = int(float(m.strip()))      # safe convert: "85.0" or "85" both work
                    full_val  = int(float(f.strip())) if f and f.strip() else 100
                except (ValueError, AttributeError):
                    continue   # skip invalid rows silently
                cursor.execute("""
                    INSERT INTO result_marks (result_id, subject, marks, full_marks)
                    VALUES (?, ?, ?, ?)
                """, (result_id, s.strip(), marks_val, full_val))
                inserted += 1
        
        if inserted == 0:
            conn.rollback()
            conn.close()
            flash("⚠️ কোনো valid marks পাওয়া যায়নি। সব marks সঠিকভাবে দিন।", "error")
            return redirect("/admin#result")
        
        conn.commit()
        conn.close()
        flash("✅ Result published successfully!", "success")
    except Exception as e:
        print(f"Result Add Error: {e}")
        flash("❌ Failed to publish result. Error: " + str(e), "error")
        
    return redirect("/admin#result")

@app.route("/gallery")
def gallery():
    photos = get_all_gallery()
    return render_template("gallery.html", photos=photos)

@app.route("/admin/gallery")
def admin_gallery():
    if "admin" not in session:
        return redirect("/login")
    photos = get_all_gallery()
    return render_template("admin_gallery.html", photos=photos)

@app.route("/admin/gallery/upload", methods=["POST"])
def upload_photo():
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if "admin" not in session:
        if is_ajax: return jsonify({"ok": False, "msg": "Unauthorized"}), 401
        return redirect("/login")

    file     = request.files.get("photo")
    title    = request.form.get("title", "").strip()
    category = request.form.get("category", "campus").strip()

    if not title:
        if is_ajax: return jsonify({"ok": False, "msg": "⚠️ Photo title is required!"})
        flash("⚠️ Photo title is required!", "error")
        return redirect("/admin/gallery")

    if not file or file.filename == "":
        if is_ajax: return jsonify({"ok": False, "msg": "⚠️ Please select a photo to upload!"})
        flash("⚠️ Please select a photo to upload!", "error")
        return redirect("/admin/gallery")

    if not allowed_file(file.filename):
        if is_ajax: return jsonify({"ok": False, "msg": "❌ Invalid file type! Only JPG, PNG, WEBP allowed."})
        flash("❌ Invalid file type! Only JPG, PNG, WEBP allowed.", "error")
        return redirect("/admin/gallery")

    # Max 5 MB check
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > 5 * 1024 * 1024:
        if is_ajax: return jsonify({"ok": False, "msg": "❌ File too large! Maximum size is 5 MB."})
        flash("❌ File too large! Maximum size is 5 MB.", "error")
        return redirect("/admin/gallery")

    try:
        os.makedirs(GALLERY_FOLDER, exist_ok=True)
        ts       = datetime.now().strftime("%Y%m%d%H%M%S_")
        filename = ts + secure_filename(file.filename)
        file.save(os.path.join(GALLERY_FOLDER, filename))

        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO gallery (title, filename, category) VALUES (?,?,?)",
            (title, filename, category)
        )
        new_id = cursor.lastrowid
        conn.commit()
        conn.close()

        if is_ajax:
            return jsonify({
                "ok": True,
                "msg": "✅ Photo uploaded successfully!",
                "photo": {
                    "id": new_id,
                    "title": title,
                    "filename": filename,
                    "category": category
                }
            })
        flash("✅ Photo uploaded successfully!", "success")
    except Exception as e:
        print(f"Upload Error: {e}")
        if is_ajax: return jsonify({"ok": False, "msg": "❌ Upload failed. Please try again."})
        flash("❌ Upload failed. Please try again.", "error")
    back = "/admin#gallery" if request.form.get('redirect_to') == 'admin' else "/admin/gallery"
    return redirect(back)

@app.route("/admin/gallery/delete/<int:photo_id>")
def delete_photo(photo_id):
    if "admin" not in session:
        return redirect("/login")
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute("SELECT filename FROM gallery WHERE id=?", (photo_id,))
        row = cursor.fetchone()

        if row:
            filepath = os.path.join(GALLERY_FOLDER, row[0])
            if os.path.exists(filepath):
                os.remove(filepath)
            cursor.execute("DELETE FROM gallery WHERE id=?", (photo_id,))
            conn.commit()
            flash("🗑️ Photo deleted!", "success")
        else:
            flash("❌ Photo not found.", "error")
        conn.close()
    except Exception as e:
        print(f"Delete Photo Error: {e}")
        flash("❌ Failed to delete photo.", "error")
    back = "/admin#gallery" if request.args.get('from') == 'admin' else "/admin/gallery"
    return redirect(back)

# ---- DB Init (merged from train.py) ----

def init_db():
    """Initialize all tables and seed default data (merged from train.py)."""
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()

        # Admin table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin (
                username TEXT PRIMARY KEY,
                password TEXT
            )
        """)

        # Admissions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                mobile TEXT NOT NULL,
                level TEXT NOT NULL,
                discipline TEXT NOT NULL,
                course TEXT NOT NULL,
                program TEXT NOT NULL,
                auth_check INTEGER DEFAULT 1,
                date_submitted DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Q&A table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS qa (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT,
                answer   TEXT
            )
        """)

        # Chat Log table — stores every conversation
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_log (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                answer   TEXT NOT NULL,
                source   TEXT DEFAULT 'bot',
                status   TEXT DEFAULT 'answered',
                asked_at TEXT
            )
        """)

        # Notices table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notices (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT NOT NULL,
                body       TEXT,
                category   TEXT DEFAULT 'General',
                department TEXT DEFAULT 'General',
                date       TEXT,
                link       TEXT
            )
        """)

        # Gallery table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS gallery (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                title    TEXT NOT NULL,
                filename TEXT NOT NULL,
                category TEXT DEFAULT 'campus'
            )
        """)

        # Library — users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lib_users (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                name     TEXT NOT NULL,
                user_id  TEXT UNIQUE NOT NULL,
                reg_no   TEXT DEFAULT '',
                password TEXT NOT NULL,
                role     TEXT DEFAULT 'student',
                dept     TEXT DEFAULT '',
                email    TEXT DEFAULT '',
                phone    TEXT DEFAULT '',
                status   TEXT DEFAULT 'active',
                created  TEXT
            )
        """)

        # Results — student results master table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS student_results (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                reg_no       TEXT UNIQUE NOT NULL,
                student_name TEXT NOT NULL,
                department   TEXT NOT NULL,
                semester     TEXT NOT NULL,
                exam_name    TEXT NOT NULL,
                created_at   TEXT
            )
        """)

        # Results — individual subject marks
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS result_marks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                result_id   INTEGER,
                subject     TEXT NOT NULL,
                marks       INTEGER NOT NULL,
                full_marks  INTEGER DEFAULT 100,
                FOREIGN KEY(result_id) REFERENCES student_results(id) ON DELETE CASCADE
            )
        """)

        # Library — ebooks table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ebooks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                author      TEXT DEFAULT '',
                description TEXT DEFAULT '',
                dept        TEXT NOT NULL,
                subject     TEXT NOT NULL,
                semester    TEXT DEFAULT '',
                filename    TEXT NOT NULL,
                uploaded_by TEXT DEFAULT '',
                upload_date TEXT,
                downloads   INTEGER DEFAULT 0,
                reads       INTEGER DEFAULT 0,
                filesize    TEXT DEFAULT '',
                filetype    TEXT DEFAULT 'pdf',
                drive_link  TEXT DEFAULT '',
                book_subject TEXT DEFAULT ''
            )
        """)
        # Migration: add drive_link and book_subject columns if missing (for existing databases)
        try:
            cursor.execute("ALTER TABLE ebooks ADD COLUMN drive_link TEXT DEFAULT ''")
        except Exception:
            pass  # column already exists
        try:
            cursor.execute("ALTER TABLE ebooks ADD COLUMN book_subject TEXT DEFAULT ''")
        except Exception:
            pass  # column already exists
        # Migration: add source column to qa table (for existing databases)
        try:
            cursor.execute("ALTER TABLE qa ADD COLUMN source TEXT DEFAULT 'manual'")
        except Exception:
            pass  # column already exists

        # Library — read history table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS read_history (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                book_id INTEGER,
                action  TEXT,
                date    TEXT
            )
        """)

        # Grievances
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS grievances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT,
                name TEXT,
                gender TEXT,
                registration_no TEXT,
                admission_no TEXT,
                email TEXT,
                phone TEXT,
                department TEXT,
                semester TEXT,
                ward_name TEXT,
                ward_registration_no TEXT,
                ward_admission_no TEXT,
                explanation TEXT,
                status TEXT,
                timestamp TEXT
            )
        """)

        # Default library admin
        lib_admin_pw = hashlib.sha256('admin@123'.encode()).hexdigest()
        cursor.execute("SELECT id FROM lib_users WHERE user_id='LIB-ADMIN'")
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO lib_users(name,user_id,password,role,status,created) VALUES(?,?,?,?,?,?)",
                ('Library Admin', 'LIB-ADMIN', lib_admin_pw, 'admin', 'active',
                 datetime.now().strftime('%Y-%m-%d'))
            )


        # Default admin (INSERT OR IGNORE so existing data is not overwritten)
        cursor.execute("INSERT OR IGNORE INTO admin VALUES ('admin','admin123')")

        # Sample chatbot Q&A data (only insert if table is empty)
        cursor.execute("SELECT COUNT(*) FROM qa")
        if cursor.fetchone()[0] == 0:
            sample_data = [
                ("What is the admission process?",   "Fill the online form and submit required documents to the admission office."),
                ("What is the fee structure?",        "The annual fee is approximately ₹35,000. Contact the office for exact details."),
                ("What courses are offered?",         "We offer Diploma in Mechanical, Civil, Electrical, Electronics & Telecom, CST, Chemical Engineering, and Pharmacy."),
                ("What are college timings?",         "College hours are 9 AM to 4 PM, Monday to Saturday."),
                ("How to contact college?",           "Call us at 03220-255462 or email helpdesk@contaipolytechnic.ac.in"),
                ("Where is Contai Polytechnic located?", "Contai Polytechnic is located in Contai, Purba Medinipur, West Bengal."),
                ("Is the college approved by WBSCTE?",  "Yes, the college is approved by WBSCTE and PCI (for Pharmacy)."),
                ("What is the total intake capacity?",   "The total annual intake is 420 seats across all departments."),
                ("When was the college established?",    "Contai Polytechnic was established in 1991."),
                ("How many departments are there?",      "There are 7 departments: Mechanical, Civil, Electrical, Electronics & Telecom, CST, Chemical, and Pharmacy."),
                ("Is there a hostel facility?",          "Please contact the college office directly for hostel availability."),
                ("How to get a scholarship?",            "Scholarships are available through the West Bengal government. Contact the college office for details."),
                ("What is the placement record?",        "The college has a strong focus on placement and maintains ties with several industries."),
                ("How to download hall ticket?",         "Hall tickets can be downloaded from the WBSCTE official portal: webscte.co.in"),
                ("What documents are needed for admission?", "You need marksheets, transfer certificate, Aadhaar card, passport photo, and caste certificate if applicable."),
            ]
            cursor.executemany("INSERT INTO qa (question, answer) VALUES (?, ?)", sample_data)
            print("OK: Sample Q&A data seeded.")

        conn.commit()
        conn.close()
        print("OK: Database initialized successfully.")
    except Exception as e:
        print(f"DB Init Error: {e}")


# ---- AICTE Documents ----
@app.route('/aicte-documents')
def aicte_documents():
    return render_template('aicte_documents.html')

# ---- New Facilities ----
@app.route('/library')
def library():
    return render_template('library.html')

@app.route('/modern-labs')
def modern_labs():
    return render_template('modern_labs.html')

@app.route('/computer-labs')
def computer_labs():
    return render_template('computer_labs.html')

@app.route('/workshop')
def workshop():
    return render_template('workshop.html')

@app.route('/auditorium')
def auditorium():
    return render_template('auditorium.html')

@app.route('/wifi-campus')
def wifi_campus():
    return render_template('wifi_campus.html')

@app.route('/grievance')
def grievance():
    return render_template('grievance.html')

@app.route('/submit-grievance', methods=['POST'])
def submit_grievance():
    type_ = request.form.get('category')
    name = request.form.get('name', '')
    gender = request.form.get('gender', '')
    registration_no = request.form.get('registration_no', '')
    admission_no = request.form.get('admission_no', '')
    email = request.form.get('email', '')
    phone = request.form.get('phone', '')
    department = request.form.get('department', '')
    semester = request.form.get('semester', '')
    ward_name = request.form.get('ward_name', '')
    ward_registration_no = request.form.get('ward_registration_no', '')
    ward_admission_no = request.form.get('ward_admission_no', '')
    explanation = request.form.get('explanation', '')
    status = 'Pending'
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        conn = sqlite3.connect("database.db")
        c = conn.cursor()
        c.execute('''
            INSERT INTO grievances 
            (type, name, gender, registration_no, admission_no, email, phone, department, semester, ward_name, ward_registration_no, ward_admission_no, explanation, status, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (type_, name, gender, registration_no, admission_no, email, phone, department, semester, ward_name, ward_registration_no, ward_admission_no, explanation, status, timestamp))
        conn.commit()
    except Exception as e:
        print("Error saving grievance:", e)
    finally:
        if conn: conn.close()
        
    flash("Your grievance has been submitted successfully.", "success")
    return redirect('/grievance')

@app.route('/api/admin/grievances/<int:id>/status', methods=['POST'])
def update_grievance_status(id):
    if "admin" not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    new_status = request.form.get('status')
    try:
        conn = sqlite3.connect("database.db")
        c = conn.cursor()
        c.execute("UPDATE grievances SET status = ? WHERE id = ?", (new_status, id))
        conn.commit()
    except Exception as e:
        print("Error updating status:", e)
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if conn: conn.close()
    return jsonify({'success': True})

@app.route('/api/admin/grievances/<int:id>', methods=['DELETE'])
def delete_grievance(id):
    if "admin" not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        conn = sqlite3.connect("database.db")
        c = conn.cursor()
        c.execute("DELETE FROM grievances WHERE id = ?", (id,))
        conn.commit()
    except Exception as e:
        print("Error deleting grievance:", e)
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if conn: conn.close()
    return jsonify({'success': True})

# ---- Start ----
if __name__ == "__main__":
    init_db()          # merged from train.py — creates tables + seeds data
    init_notices_table()
    init_gallery_table()
    init_homepage_tables()
    load_model()
    print("OK: Website ready at http://127.0.0.1:5000")
    print("OK: Voice assistant ready at http://127.0.0.1:5000/voice")
    app.run(debug=True, use_reloader=False)
