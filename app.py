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

# =====================
# UI
# =====================
st.title("🏗️ 施工管理AIツール (データ解析版)")

col1, col2 = st.columns([1, 2])

# --- 左カラム: 案件・ルール管理 ---
with col1:
    with st.expander("🏢 企業共通ルール"):
        with get_db() as conn:
            c_set = conn.execute("SELECT common_rule FROM company_settings WHERE id = 1").fetchone()
            current_co_rule = c_set["common_rule"] if c_set else ""
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
            st.rerun()

# --- 右カラム: メイン機能 ---
with col2:
    tabs = st.tabs(["📊 データ解析", "🔄 差分抽出", "💎 マスター", "⚠️ 事故DB", "📖 用語辞典"])
   
    with get_db() as conn:
        acc_list = conn.execute("SELECT content FROM accidents").fetchall()
        acc_context = "\n".join([f"・{r['content']}" for r in acc_list])

    # 1. 手順書解析（データ解析）
    with tabs[0]:
        st.subheader("手順書の安全・ルール解析")
        f_man = st.file_uploader("解析する手順書(PDF)", type="pdf")
        if f_man and st.button("🔍 解析を実行"):
            text = read_pdf(f_man)
            st.session_state.current_text = text
            prompt = f"""施工管理の専門家として手順書を解析せよ。
            ルールや事故DB({acc_context})に基づき、記載漏れやリスクを指摘すること。
            特に作業内容から推測される安全対策（安全帯、立会い、検電など）の不足を重点的に確認せよ。
           
            手順書内容:
            {text}"""
            st.session_state.ans = ask_ai([{"role":"user", "content":prompt}])
        if "ans" in st.session_state: st.info(st.session_state.ans)

    # 2. 差分抽出（チェックボックス復活！）
    with tabs[1]:
        st.subheader("新旧手順書の比較・マスター反映")
        f_old = st.file_uploader("旧版(PDF)", type="pdf", key="f_old")
        f_new = st.file_uploader("新版(PDF)", type="pdf", key="f_new")
       
        if f_old and f_new and st.button("🔄 差分を抽出"):
            t_old, t_new = read_pdf(f_old), read_pdf(f_new)
            prompt = f"以下の施工手順書の新旧比較を行い、変更点や新しい決定事項を箇条書き（各行の先頭は「・」）で抽出せよ。\n旧:{t_old}\n新:{t_new}"
            raw_diff = ask_ai([{"role":"user", "content":prompt}])
            # 箇条書きをリスト化
            st.session_state.diff_items = [line.strip() for line in raw_diff.split('\n') if line.strip().startswith('・')]
       
        if "diff_items" in st.session_state and st.session_state.diff_items:
            st.write("反映したい項目にチェックを入れてね：")
            selected_items = []
            for i, item in enumerate(st.session_state.diff_items):
                if st.checkbox(item, key=f"diff_check_{i}", value=True):
                    selected_items.append(item)
           
            if st.button("選んだ項目をマスターに反映"):
                if all_p:
                    new_master = (p_data["master_content"] or "") + "\n" + "\n".join(selected_items)
                    with get_db() as conn:
                        conn.execute("UPDATE projects SET master_content=? WHERE id=?", (new_master.strip(), p_id))
                        conn.commit()
                    st.success("マスターに追記したよ！")
                    del st.session_state.diff_items # リセット
                    st.rerun()

    # 3. マスター管理
    with tabs[2]:
        st.subheader("決定事項（マスター）")
        if all_p:
            m_val = st.text_area("この案件の決定事項", p_data["master_content"], height=400)
            if st.button("マスターを直接保存"):
                with get_db() as conn:
                    conn.execute("UPDATE projects SET master_content=? WHERE id=?", (m_val, p_id))
                    conn.commit()
                st.success("保存したよ")

    # 4. 事故DB
    with tabs[3]:
        st.subheader("⚠️ 事故DB")
        f_acc = st.file_uploader("事故報告PDF追加", type="pdf")
        if f_acc and st.button("DB登録"):
            summary = ask_ai([{"role":"user", "content":f"事故の状況と対策を簡潔にまとめろ:\n{read_pdf(f_acc)}"}])
            with get_db() as conn:
                conn.execute("INSERT INTO accidents (content) VALUES (?)", (summary,))
                conn.commit()
            st.rerun()
        with get_db() as conn:
            for r in conn.execute("SELECT * FROM accidents").fetchall():
                st.write(f"・{r['content']}")

    # 5. 用語辞典
    with tabs[4]:
        st.subheader("📖 現場用語")
        w = st.text_input("用語")
        m = st.text_input("意味")
        if st.button("辞典登録"):
            with get_db() as conn:
                conn.execute("INSERT OR REPLACE INTO dictionary (word, meaning) VALUES (?,?)", (w, m))
                conn.commit()
            st.rerun()
        with get_db() as conn:
            for r in conn.execute("SELECT * FROM dictionary").fetchall():
                st.write(f"**{r['word']}**: {r['meaning']}")
