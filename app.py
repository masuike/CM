import streamlit as st
import pandas as pd
import sqlite3
import os
from openai import OpenAI
import PyPDF2

# =====================
# 🔐 初期設定
# =====================
PASSWORD = os.getenv("APP_PASSWORD", "test123")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
DATA_DIR = "data"
if not os.path.exists(DATA_DIR): os.makedirs(DATA_DIR)

if "auth" not in st.session_state:
    st.session_state.auth = False

if not st.session_state.auth:
    st.title("🔐 ログイン")
    pw = st.text_input("パスワード", type="password")
    if st.button("ログイン"):
        if pw == PASSWORD:
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("パスワードが違います")
    st.stop()

st.set_page_config(page_title="施工管理AIツール", layout="wide")

# =====================
# 🗄️ データベース管理
# =====================
DB_PATH = "construction_ai.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT,
                building_name TEXT,
                building_rule TEXT DEFAULT '',
                project_rule TEXT DEFAULT '',
                master_content TEXT DEFAULT '',
                UNIQUE(project_name, building_name)
            )
        """)
        conn.execute("CREATE TABLE IF NOT EXISTS company_settings (id INTEGER PRIMARY KEY, common_rule TEXT DEFAULT '')")
        conn.execute("INSERT OR IGNORE INTO company_settings (id, common_rule) VALUES (1, '')")
        conn.execute("CREATE TABLE IF NOT EXISTS accidents (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT)")
        conn.commit()

init_db()

# =====================
# 🛠️ PDFテキスト抽出関数
# =====================
def read_pdf(f):
    if not f: return ""
    try:
        r = PyPDF2.PdfReader(f)
        text = ""
        for page in r.pages:
            text += page.extract_text() + "\n"
        return text
    except: return "PDFの読み取りに失敗しました。"

def ask_ai(messages):
    try:
        res = client.chat.completions.create(model="gpt-4o", messages=messages, temperature=0.1)
        return res.choices[0].message.content
    except: return "AIエラーが発生しました"

# =====================
# UI
# =====================
st.title("🏗️ 施工管理AIツール (テキスト解析特化版)")

col1, col2 = st.columns([1, 2])

# --- 共通データの取得 ---
with get_db() as conn:
    c_set = conn.execute("SELECT common_rule FROM company_settings WHERE id = 1").fetchone()
    current_co_rule = c_set["common_rule"] if c_set else ""
    acc_rows = conn.execute("SELECT * FROM accidents").fetchall()
    acc_context = "\n".join([f"・{r['content']}" for r in acc_rows])

# --- 左カラム ---
with col1:
    with st.expander("🏢 企業共通ルール"):
        co_v = st.text_area("全案件共通", current_co_rule, height=80)
        if st.button("企業ルール保存"):
            with get_db() as conn:
                conn.execute("UPDATE company_settings SET common_rule = ? WHERE id = 1", (co_v,))
                conn.commit()
            st.success("保存完了")

    st.subheader("🆕 案件選択")
    with get_db() as conn:
        all_p = conn.execute("SELECT id, building_name, project_name FROM projects").fetchall()
  
    p_data = None
    if all_p:
        p_options = {f"{r['building_name']} / {r['project_name']}": r['id'] for r in all_p}
        sel_label = st.selectbox("案件を選んでね", list(p_options.keys()))
        p_id = p_options[sel_label]
        with get_db() as conn:
            p_data = conn.execute("SELECT * FROM projects WHERE id = ?", (p_id,)).fetchone()
        br_v = st.text_area("🏙️ ビル固有ルール", p_data["building_rule"], key=f"br_{p_id}", height=120)
        pr_v = st.text_area("🚧 案件固有ルール", p_data["project_rule"], key=f"pr_{p_id}", height=120)
        if st.button("案件ルール保存"):
            with get_db() as conn:
                conn.execute("UPDATE projects SET building_rule=?, project_rule=? WHERE id=?", (br_v, pr_v, p_id))
                conn.commit()
            st.success("保存完了")

# --- 右カラム ---
with col2:
    tabs = st.tabs(["📊 資料解析チャット", "⚠️ 事故DB"])
  
    with tabs[0]:
        st.subheader("💬 手順書スピード精査")
       
        up_file = st.file_uploader("PDF手順書をアップロード", type="pdf")
        if up_file and st.button("🔍 プロの視点でチェック実行"):
            pdf_text = read_pdf(up_file)
           
            with st.status("AI監督がテキストからリスクを抽出中..."):
                sys_p = f"""あなたは30年のキャリアを持つ施工管理技士です。
バラバラに抽出されたテキストから「工事の全体像」を推測し、致命的な抜け漏れを指摘してください。

【1. 資料から探すべき重要項目】
・作業開始・終了時間（どこかに時刻の記載がないか？）
・緊急連絡先（AE社、電力会社、防災センター等の電話番号）
・「分電盤OFF/開放」のステップの有無

【2. 厳格なリスクチェック】
・「分電盤OFF」がある場合：負荷側（PC、サーバー等）への周知や影響確認の記述が「一言でも」あるか？
・「他社手配（AE社等）」がある場合：合流場所や連絡先の記載があるか？
・「WhM交換」がある場合：この資料だけで完結しているか？（別日の作業が混ざっていないか）

【3. 現場ルールとの照合】
・共通ルール：{current_co_rule}
・案件ルール：{p_data['building_rule'] if p_data else ''} / {p_data['project_rule'] if p_data else ''}
※場所（階数）が違うルールは無視すること。

【出力形式】
不備は【⚠️警告】、改善アドバイスは【🔍技術的注意点】として、理由とともに具体的に出力してください。
"""
                ans = ask_ai([{"role":"system", "content":sys_p}, {"role":"user", "content":f"資料テキスト:\n{pdf_text}"}])
                st.session_state.auto_analysis = ans
            st.success("解析完了！")

        if "auto_analysis" in st.session_state:
            st.info("💡 **AI解析結果（テキスト抽出版）**")
            st.markdown(st.session_state.auto_analysis)
            if st.button("クリア"):
                del st.session_state.auto_analysis
                st.rerun()

    with tabs[1]:
        st.subheader("⚠️ 事故DB")
        for r in acc_rows: st.info(r['content'])
