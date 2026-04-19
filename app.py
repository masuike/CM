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
st.title("🏗️ 施工管理AIツール (完全統合版)")

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
            os.makedirs(os.path.join(DATA_DIR, f"{nb}_{np}"), exist_ok=True)
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
                f_path = os.path.join(DATA_DIR, project_folder, up_file.name)
                os.makedirs(os.path.dirname(f_path), exist_ok=True)
                with open(f_path, "wb") as f: f.write(up_file.getbuffer())
                st.success(f"{up_file.name} を保存！AIの記憶に追加されました。")
                st.rerun()

        if "chat_history" not in st.session_state: st.session_state.chat_history = []
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        if user_query := st.chat_input("質問を入力してください（例：今までの決定事項は？）"):
            st.session_state.chat_history.append({"role": "user", "content": user_query})
            with st.chat_message("user"): st.markdown(user_query)
            with st.chat_message("assistant"):
                sys_p = f"あなたは施工管理のプロです。資料、ルール、事故DBに基づき誠実に回答してください。\n【資料】:{project_context}\n【ルール】:{current_co_rule}\n【事故DB】:{acc_context}"
                ans = ask_ai([{"role":"system", "content":sys_p}] + st.session_state.chat_history)
                st.markdown(ans)
                st.session_state.chat_history.append({"role": "assistant", "content": ans})

    with tabs[1]:
        st.subheader("新旧手順書の比較・マスター反映")
        f_old = st.file_uploader("旧版(PDF)", type="pdf", key="f_old")
        f_new = st.file_uploader("新版(PDF)", type="pdf", key="f_new")
        if f_old and f_new and st.button("🔄 差分を抽出"):
            t_old, t_new = read_pdf(f_old), read_pdf(f_new)
            prompt = f"施工手順書の新旧比較を行い、変更点や新しい決定事項を箇条書き（・）で抽出せよ。\n旧:{t_old}\n新:{t_new}"
            st.session_state.diff_items = [l.strip() for l in ask_ai([{"role":"user", "content":prompt}]).split('\n') if l.strip().startswith('・')]
       
        if "diff_items" in st.session_state:
            st.write("反映したい項目にチェック：")
            sel_items = []
            for i, item in enumerate(st.session_state.diff_items):
                if st.checkbox(item, key=f"c_{i}", value=True): sel_items.append(item)
            if st.button("選んだ項目をマスターに反映"):
                if all_p:
                    new_m = (p_data["master_content"] or "") + "\n" + "\n".join(sel_items)
                    with get_db() as conn:
                        conn.execute("UPDATE projects SET master_content=? WHERE id=?", (new_m.strip(), p_id))
                        conn.commit()
                    st.success("反映完了！")
                    del st.session_state.diff_items
                    st.rerun()

    with tabs[2]:
        st.subheader("決定事項（マスター）")
        if all_p:
            m_val = st.text_area("この案件の決定事項", p_data["master_content"], height=400)
            if st.button("マスターを直接保存"):
                with get_db() as conn:
                    conn.execute("UPDATE projects SET master_content=? WHERE id=?", (m_val, p_id))
                    conn.commit()
                st.success("保存しました")

    with tabs[3]:
        st.subheader("⚠️ 事故DB")
        f_acc = st.file_uploader("事故報告PDF追加", type="pdf", key="acc_up")
        if f_acc and st.button("DB登録"):
            summary = ask_ai([{"role":"user", "content":f"事故の状況と対策を簡潔にまとめろ:\n{read_pdf(f_acc)}"}])
            with get_db() as conn:
                conn.execute("INSERT INTO accidents (content) VALUES (?)", (summary,))
                conn.commit()
            st.rerun()
        st.write("---")
        for r in acc_rows: st.info(r['content'])

    with tabs[4]:
        st.subheader("📖 現場用語")
        w_in = st.text_input("用語")
        m_in = st.text_input("意味")
        if st.button("辞典登録"):
            with get_db() as conn:
                conn.execute("INSERT OR REPLACE INTO dictionary (word, meaning) VALUES (?,?)", (w_in, m_in))
                conn.commit()
            st.rerun()
        st.write("---")
        with get_db() as conn:
            for r in conn.execute("SELECT * FROM dictionary").fetchall():
                st.write(f"**{r['word']}**: {r['meaning']}")
# --- メンテナンス用：データベースを初期化するボタン ---
st.sidebar.markdown("---")
if st.sidebar.button("⚠️ 全データをリセット（削除）"):
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        st.success("データベースを削除したよ。再起動してね！")
        st.rerun()
