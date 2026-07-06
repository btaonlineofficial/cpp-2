import html
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from html.parser import HTMLParser

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except Exception:
    TfidfVectorizer = None
    cosine_similarity = None


DEFAULT_GEMINI_MODEL = "gemini-flash-lite-latest"
FALLBACK_GEMINI_MODELS = (
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite",
    "gemini-flash-latest",
)
MAX_CONTEXT_CHARS = 12000


class _VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._hidden_depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "svg", "noscript"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "svg", "noscript"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data):
        if not self._hidden_depth:
            text = data.strip()
            if text and not text.startswith("{%") and not text.startswith("{{"):
                self.parts.append(text)


def _clean_text(text):
    text = html.unescape(text or "")
    text = re.sub(r"\{\#.*?\#\}", " ", text, flags=re.S)
    text = re.sub(r"\{%.*?%\}", " ", text, flags=re.S)
    text = re.sub(r"\{\{.*?\}\}", " ", text, flags=re.S)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _html_to_text(raw_html):
    parser = _VisibleTextParser()
    try:
        parser.feed(raw_html)
    except Exception:
        return _clean_text(re.sub(r"<[^>]+>", " ", raw_html))
    return _clean_text(" ".join(parser.parts))


def _load_env_file(base_dir):
    env_path = os.path.join(base_dir, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip().lstrip("\ufeff")
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as exc:
        print(f"ENV load warning: {exc}")


def _get_gemini_key(base_dir):
    _load_env_file(base_dir)
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    key_file = os.environ.get("GEMINI_API_KEY_FILE", "").strip()
    if not key and key_file and os.path.exists(key_file):
        try:
            with open(key_file, "r", encoding="utf-8") as f:
                key = f.read().strip()
        except Exception as exc:
            print(f"Gemini key file warning: {exc}")
    return key


def gemini_config_status(base_dir):
    key = _get_gemini_key(base_dir)
    model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    return {
        "configured": bool(key),
        "model": model,
        "key_suffix": key[-4:] if key else "",
    }


def _fetch_rows(db_path, table, columns, order_by=None, limit=None):
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        if not cur.fetchone():
            return []
        selected = ", ".join(columns)
        sql = f"SELECT {selected} FROM {table}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        cur.execute(sql)
        return [dict(row) for row in cur.fetchall()]
    except Exception as exc:
        print(f"RAG DB read warning ({table}): {exc}")
        return []
    finally:
        conn.close()


def _add_doc(docs, title, body, source):
    body = _clean_text(body)
    if body:
        docs.append({"title": title, "body": body, "source": source})


def _build_database_docs(db_path):
    docs = []

    for row in _fetch_rows(db_path, "qa", ["question", "answer"], "id DESC"):
        _add_doc(
            docs,
            f"Q&A: {row.get('question', '')}",
            f"Question: {row.get('question', '')}\nAnswer: {row.get('answer', '')}",
            "database:qa",
        )

    for row in _fetch_rows(db_path, "notices", ["title", "body", "category", "department", "date", "link"], "id DESC", 50):
        _add_doc(
            docs,
            f"Notice: {row.get('title', '')}",
            (
                f"Notice title: {row.get('title', '')}\n"
                f"Category: {row.get('category', '')}\n"
                f"Department: {row.get('department', '')}\n"
                f"Date: {row.get('date', '')}\n"
                f"Details: {row.get('body', '')}\n"
                f"Link: {row.get('link', '')}"
            ),
            "database:notices",
        )

    for row in _fetch_rows(db_path, "hp_events", ["day", "month", "tag", "title", "description", "location"], "id DESC", 50):
        _add_doc(
            docs,
            f"Event: {row.get('title', '')}",
            (
                f"Event: {row.get('title', '')}\n"
                f"Date: {row.get('day', '')} {row.get('month', '')}\n"
                f"Type: {row.get('tag', '')}\n"
                f"Location: {row.get('location', '')}\n"
                f"Details: {row.get('description', '')}"
            ),
            "database:hp_events",
        )

    for row in _fetch_rows(db_path, "hp_faculty", ["name", "department", "designation", "qualification", "years_exp", "papers", "students"], "id ASC"):
        _add_doc(
            docs,
            f"Faculty: {row.get('name', '')}",
            (
                f"Faculty name: {row.get('name', '')}\n"
                f"Department: {row.get('department', '')}\n"
                f"Designation: {row.get('designation', '')}\n"
                f"Qualification: {row.get('qualification', '')}\n"
                f"Experience: {row.get('years_exp', '')}\n"
                f"Papers: {row.get('papers', '')}\n"
                f"Students guided: {row.get('students', '')}"
            ),
            "database:hp_faculty",
        )

    placement = _fetch_rows(db_path, "hp_placement", ["key", "value"], "key ASC")
    if placement:
        _add_doc(
            docs,
            "Placement statistics",
            "\n".join(f"{row.get('key', '')}: {row.get('value', '')}" for row in placement),
            "database:hp_placement",
        )

    companies = _fetch_rows(db_path, "hp_companies", ["name"], "id ASC")
    if companies:
        _add_doc(
            docs,
            "Placement companies",
            "Recruiting companies: " + ", ".join(row.get("name", "") for row in companies),
            "database:hp_companies",
        )

    for row in _fetch_rows(db_path, "hp_alumni", ["name", "quote", "role", "batch"], "id DESC", 50):
        _add_doc(
            docs,
            f"Alumni: {row.get('name', '')}",
            (
                f"Alumni name: {row.get('name', '')}\n"
                f"Role: {row.get('role', '')}\n"
                f"Batch: {row.get('batch', '')}\n"
                f"Quote: {row.get('quote', '')}"
            ),
            "database:hp_alumni",
        )

    for row in _fetch_rows(db_path, "lib_departments", ["id", "name", "heading"], "name ASC"):
        _add_doc(
            docs,
            f"Library department: {row.get('name', '')}",
            f"Library department/category: {row.get('name', '')}. Group: {row.get('heading', '')}. ID: {row.get('id', '')}",
            "database:lib_departments",
        )

    for row in _fetch_rows(db_path, "lib_subjects", ["name", "dept_id"], "name ASC"):
        _add_doc(
            docs,
            f"Library subject: {row.get('name', '')}",
            f"Subject: {row.get('name', '')}. Department/category ID: {row.get('dept_id', '')}",
            "database:lib_subjects",
        )

    for row in _fetch_rows(db_path, "ebooks", ["title", "author", "description", "dept", "subject", "semester", "filetype"], "upload_date DESC", 80):
        _add_doc(
            docs,
            f"Library book: {row.get('title', '')}",
            (
                f"Book title: {row.get('title', '')}\n"
                f"Author: {row.get('author', '')}\n"
                f"Department: {row.get('dept', '')}\n"
                f"Subject: {row.get('subject', '')}\n"
                f"Semester: {row.get('semester', '')}\n"
                f"Type: {row.get('filetype', '')}\n"
                f"Description: {row.get('description', '')}"
            ),
            "database:ebooks",
        )

    return docs


def _build_website_docs(base_dir):
    docs = []
    template_dir = os.path.join(base_dir, "new_templates")
    if not os.path.isdir(template_dir):
        return docs

    skip_prefixes = ("admin",)
    skip_parts = {os.path.join("library", "admin")}

    for root, _, files in os.walk(template_dir):
        for filename in files:
            if not filename.endswith(".html"):
                continue
            rel = os.path.relpath(os.path.join(root, filename), template_dir)
            rel_norm = rel.replace("\\", "/")
            if filename.startswith(skip_prefixes) or any(part in rel for part in skip_parts):
                continue
            if rel_norm.startswith("library/admin"):
                continue
            try:
                with open(os.path.join(root, filename), "r", encoding="utf-8", errors="ignore") as f:
                    text = _html_to_text(f.read())
            except Exception as exc:
                print(f"Template read warning ({rel}): {exc}")
                continue
            if len(text) > 300:
                _add_doc(docs, f"Website page: {rel_norm}", text[:5000], f"website:{rel_norm}")

    return docs


def build_knowledge_docs(base_dir, db_path):
    docs = _build_database_docs(db_path)
    docs.extend(_build_website_docs(base_dir))
    return docs


def find_context_documents(question, base_dir, db_path, top_k=8):
    docs = build_knowledge_docs(base_dir, db_path)
    if not docs:
        return []

    texts = [f"{doc['title']}\n{doc['body']}" for doc in docs]
    if TfidfVectorizer is None or cosine_similarity is None:
        query_terms = set(re.findall(r"[a-zA-Z0-9]+", question.lower()))
        ranked = []
        for index, text in enumerate(texts):
            doc = docs[index]
            body_terms = set(re.findall(r"[a-zA-Z0-9]+", text.lower()))
            title_terms = set(re.findall(r"[a-zA-Z0-9]+", doc["title"].lower()))
            source_terms = set(re.findall(r"[a-zA-Z0-9]+", doc["source"].lower()))
            score = (
                len(query_terms & body_terms)
                + 3 * len(query_terms & title_terms)
                + 2 * len(query_terms & source_terms)
            ) / max(len(query_terms), 1)
            ranked.append((index, score))
        ranked.sort(key=lambda item: item[1], reverse=True)
        selected = []
        for index, score in ranked[:top_k]:
            if score <= 0 and selected:
                continue
            doc = dict(docs[index])
            doc["score"] = float(score)
            selected.append(doc)
        return selected

    try:
        vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=9000)
        vectors = vectorizer.fit_transform(texts)
        query_vector = vectorizer.transform([question])
        scores = cosine_similarity(query_vector, vectors)[0]
        query_terms = set(re.findall(r"[a-zA-Z0-9]+", question.lower()))
        for index, doc in enumerate(docs):
            title_terms = set(re.findall(r"[a-zA-Z0-9]+", doc["title"].lower()))
            source_terms = set(re.findall(r"[a-zA-Z0-9]+", doc["source"].lower()))
            scores[index] += 0.04 * len(query_terms & title_terms)
            scores[index] += 0.03 * len(query_terms & source_terms)
    except Exception as exc:
        print(f"RAG ranking warning: {exc}")
        return docs[:top_k]

    ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
    selected = []
    for index, score in ranked[:top_k]:
        if score <= 0 and selected:
            continue
        doc = dict(docs[index])
        doc["score"] = float(score)
        selected.append(doc)
    return selected


def _context_text(docs):
    chunks = []
    total = 0
    for i, doc in enumerate(docs, 1):
        block = f"[{i}] {doc['title']} ({doc['source']})\n{doc['body']}"
        if total + len(block) > MAX_CONTEXT_CHARS:
            break
        chunks.append(block)
        total += len(block)
    return "\n\n".join(chunks)


def _gemini_request(api_key, model, prompt):
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.25,
            "topP": 0.9,
            "maxOutputTokens": 600,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=18) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    candidates = result.get("candidates", [])
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    return "\n".join(part.get("text", "") for part in parts).strip()


def answer_with_gemini(question, base_dir, db_path):
    docs = find_context_documents(question, base_dir, db_path)
    if not docs:
        return ""

    api_key = _get_gemini_key(base_dir)
    if not api_key:
        return ""

    configured_model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    prompt = f"""
You are the AI helpdesk assistant for Contai Polytechnic.

Never introduce yourself with a personal name. Do not use the name "Nova" or any other assistant name.
Do not say "I am Nova". If a greeting is needed, simply say "Hello" or answer directly.
Write in a professional helpdesk tone: clear, polite, direct, and useful.
Do not use markdown formatting such as **bold**, bullet symbols, headings, or decorative punctuation. Use simple plain text sentences that sound natural when spoken aloud.

For Contai Polytechnic specific facts, use the provided college database and website context first. Do not invent college-specific facts such as fees, dates, notices, staff names, phone numbers, admissions rules, departments, results, or schedules.
If the user asks a general study, science, technology, career, exam-preparation, definition, or normal knowledge question that is not college-specific, answer from your general knowledge in a helpful student-friendly way.
If the user asks for a college-specific detail that is not present in the context, say that the detail is not available in the college database and suggest contacting the college helpdesk.
Answer in the same language as the user's question when possible. Keep it concise, clear, and helpful.
For admission, departments, library, notice, event, faculty, placement, address, phone, and timing questions, prefer exact details from the context.

User question:
{question}

College context:
{_context_text(docs)}

Final answer:
""".strip()

    models = [configured_model]
    for fallback_model in FALLBACK_GEMINI_MODELS:
        if fallback_model not in models:
            models.append(fallback_model)

    for model in models:
        try:
            answer = _gemini_request(api_key, model, prompt)
            if answer:
                return answer
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                detail = str(exc)
            print(f"Gemini HTTP error ({model}): {exc.code} {detail[:500]}")
            if exc.code not in (404, 429):
                break
        except Exception as exc:
            print(f"Gemini request error ({model}): {exc}")
            break
    return ""
