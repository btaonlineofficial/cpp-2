import app

app.init_db()
app.init_notices_table()
app.init_gallery_table()
app.init_homepage_tables()
app.load_model()
app.app.run(debug=False, use_reloader=False)
