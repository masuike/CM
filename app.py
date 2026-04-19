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
        conn.execute("CREATE TABLE IF NOT EXISTS dictionary (id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT UNIQUE, meaning TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS accidents (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT)")
        conn.commit()

init_db()

# =====================
# 共通関数
# =====================
def read_pdf(f):
    if not f: return ""
    try:
        r = PyPDF2.PdfReader(f)
        return "".join([p.extract_text() or "" for p in r.pages])
    except: return ""

def ask_ai(messages):
    try:
        res = client.chat.completions.create(model="gpt-4o-mini", messages=messages, temperature=0)
        return res.choices[0].message.content
    except: return "AIエラーが発生しました"

def get_project_docs(project_folder_name):
    folder_path = os.path.join(DATA_DIR, project_folder_name)
    all_text = ""
    if os.path.exists(folder_path):
        for file in os.listdir(folder_path):
            if file.endswith(".pdf"):
                with open(os.path.join(folder_path, file), "rb") as f:
                    all_text += f"\n--- 資料: {file} ---\n"
                    all_text += read_pdf(f)
    return all_text

# =====================
# UI
# =====================
st.title("🏗️ 施工管理AIツール (データ解析版)")

col1, col2 = st.columns([1, 2])

# --- 共通データの取得 ---
with get_db() as conn:
    c_set = conn.execute("SELECT common_rule FROM company_settings WHERE id = 1").fetchone()
    current_co_rule = c_set["common_rule"] if c_set else ""
    acc_list = conn.execute("SELECT content FROM accidents").fetchall()
    acc_context = "\n".join([f"・{r['content']}" for r in acc_list])

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
  
    if all_p:
        p_options = {f"{r['building_name']} / {r['project_name']}": r['id'] for r in all_p}
        sel_label = st.selectbox("案件を選んでね", list(p_options.keys()))
        p_id = p_options[sel_label]
      
        with get_db() as conn:
            p_data = conn.execute("SELECT * FROM projects WHERE id = ?", (p_id,)).fetchone()
       
        project_folder = f"{p_data['building_name']}_{p_data['project_name']}"
      
        br_v = st.text_area("🏙️ ビル固有ルール", p_data["building_rule"], key=f"br_{p_id}")
        pr_v = st.text_area("🚧 案件固有ルール", p_data["project_rule"], key=f"pr_{p_id}")
        if st.button("案件ルール保存"):
            with get_db() as conn:
                conn.execute("UPDATE projects SET building_rule=?, project_rule=? WHERE id=?", (br_v, pr_v, p_id))
                conn.commit()
            st.success("保存完了")
  
    with st.expander("＋ 新規案件追加"):
        nb = st.text_input("ビル名")
        np = st.text_input("案件名")
        if st.button("登録"):
            with get_db() as conn:
                conn.execute("INSERT INTO projects (building_name, project_name) VALUES (?, ?)", (nb, np))
                conn.commit()
            new_folder = os.path.join(DATA_DIR, f"{nb}_{np}")
            os.makedirs(new_folder, exist_ok=True)
            st.rerun()

# --- 右カラム ---
with col2:
    tabs = st.tabs(["📊 資料解析チャット", "🔄 差分抽出", "💎 マスター", "⚠️ 事故DB", "📖 用語辞典"])
  
    with tabs[0]:
        st.subheader(f"💬 {sel_label if all_p else ''} の相談窓口")
        project_context = get_project_docs(project_folder) if all_p else ""
       
        with st.expander("📁 資料を追加アップロード"):
            up_file = st.file_uploader("PDFを選択", type="pdf", key="pro_up")
            if up_file and st.button("案件フォルダに保存"):
                folder_path = os.path.join(DATA_DIR, project_folder)
                os.makedirs(folder_path, exist_ok=True)
                with open(os.path.join(folder_path, up_file.name), "wb") as f:
                    f.write(up_file.getbuffer())
                st.success(f"{up_file.name} を保存しました！")
                st.rerun()

        if "chat_history" not in st.session_state:
            st.session_state.chat_history = []
       
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])

        if user_query := st.chat_input("質問を入力..."):
            st.session_state.chat_history.append({"role": "user", "content": user_query})
            with st.chat_message("user"): st.markdown(user_query)
            with st.chat_message("assistant"):
                sys_p = f"施工プロとして回答せよ。\n資料:{project_context}\n共通ルール:{current_co_rule}\n事故DB:{acc_context}"
                ans = ask_ai([{"role":"system", "content":sys_p}] + st.session_state.chat_history)
                st.markdown(ans)
                st.session_state.chat_history.append({"role": "assistant", "content": ans})

    with tabs[1]:
        st.subheader("新旧比較")
        f_old = st.file_uploader("旧版", type="pdf", key="f_old")
        f_new = st.file_uploader("新版", type="pdf", key="f_new")
        if f_old and f_new and st.button("🔄 抽出"):
            t_old, t_new = read_pdf(f_old), read_pdf(f_new)
            prompt = f"差分を抽出せよ。\n旧:{t_old}\n新:{t_new}"
            st.session_state.diff_items = [l.strip() for l in ask_ai([{"role":"user", "content":prompt}]).split('\n') if l.strip().startswith('・')]
        if "diff_items" in st.session_state:
            for i, item in enumerate(st.session_state.diff_items):
                st.checkbox(item, key=f"c_{i}", value=True)

    with tabs[2]:
        if all_p: st.text_area("決定事項", p_data["master_content"], height=300)

    with tabs[3]:
        st.subheader("⚠️ 事故DB")
        for r in acc_list: st.write(f"・{r['content']}")

    with tabs[4]:
        st.subheader("📖 用語辞典")
        with get_db() as conn:
            for r in conn.execute("SELECT * FROM dictionary").fetchall():
                st.write(f"**{r['word']}**: {r['meaning']}")
