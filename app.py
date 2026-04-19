import streamlit as st
import pandas as pd
import sqlite3
import os
from openai import OpenAI

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

def ask_ai(messages):
    try:
        res = client.chat.completions.create(model="gpt-4o", messages=messages, temperature=0.1)
        return res.choices[0].message.content
    except: return "AIエラーが発生しました"

# =====================
# UI
# =====================
st.title("🏗️ 施工管理AIツール (コピペ解析版)")

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
    tabs = st.tabs(["📊 手順書コピペ解析", "⚠️ 事故DB"])
  
    with tabs[0]:
        st.subheader("📝 手順書テキスト貼り付け")
        st.write("PDFを全選択(Ctrl+A)して、下の枠に貼り付けてね。")
       
        # テキスト入力エリア
        manual_text = st.text_area("ここに手順書の内容を貼り付け", height=300, placeholder="11. 緊急連絡先... 14. 作業手順...")
       
        if st.button("🚀 プロの視点で精査開始"):
            if not manual_text:
                st.warning("テキストを貼り付けてからボタンを押してね。")
            else:
                with st.status("AI監督が内容を徹底チェック中..."):
                    sys_p = f"""あなたは熟練の施工管理技士です。
貼り付けられた「手順書テキスト」から、現場でトラブルになりそうなポイントを厳しく指摘してください。

【1. 指標】
・緊急連絡先（AE社、電力、防災センター等）の具体的な番号があるか？
・作業開始・終了時間の記載があるか？
・「分電盤OFF/ブレーカー開放」の際に、負荷側（PC、サーバー、設備）への停電周知や影響確認の記述があるか？

【2. ルール照合】
・共通ルール：{current_co_rule}
・案件ルール：{p_data['building_rule'] if p_data else ''} / {p_data['project_rule'] if p_data else ''}
※「2階」の作業なら「5階」のルールは無視するなど、場所の整合性も見てください。

【3. 出力】
不備やリスクがあれば【⚠️警告】、改善案は【🔍技術的注意点】として出力してください。
"""
                    ans = ask_ai([{"role":"system", "content":sys_p}, {"role":"user", "content":f"手順書テキスト:\n{manual_text}"}])
                    st.session_state.auto_analysis = ans
                st.success("精査完了！")

        if "auto_analysis" in st.session_state:
            st.info("💡 **AI精査結果**")
            st.markdown(st.session_state.auto_analysis)
            if st.button("クリア"):
                del st.session_state.auto_analysis
                st.rerun()

    with tabs[1]:
        st.subheader("⚠️ 事故DB")
        for r in acc_rows: st.info(r['content'])
