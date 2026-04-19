import streamlit as st
import pandas as pd
import sqlite3
import os
import base64
from io import BytesIO
from openai import OpenAI
from PIL import Image
import PyPDF2
from pdf2image import convert_from_bytes

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
        else: st.error("パスワードが違います")
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
        conn.execute("CREATE TABLE IF NOT EXISTS dictionary (id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT, mean TEXT)")
        conn.commit()

init_db()

# =====================
# 👁️ 解析・AI関数
# =====================
def ask_ai(messages):
    try:
        # タイムアウトを防ぐため、ここで確実にレスポンスを待つ
        res = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.1,
            timeout=60.0  # 60秒まで待つ設定
        )
        return res.choices[0].message.content
    except Exception as e:
        return f"❌ AIエラー: {str(e)}"

# =====================
# UI構成
# =====================
st.title("🏗️ 施工管理AIツール (安定動作版)")

# 共通ルールの事前取得
with get_db() as conn:
    c_set = conn.execute("SELECT common_rule FROM company_settings WHERE id = 1").fetchone()
    cur_co = c_set["common_rule"] if c_set else ""

col1, col2 = st.columns([1, 2])

# --- 左カラム：設定 ---
with col1:
    with st.expander("🏢 共通ルール・辞書"):
        t_sub = st.tabs(["ルール", "辞書"])
        with t_sub[0]:
            co_v = st.text_area("全案件共通", cur_co, height=80, key="co_rule_input")
            if st.button("ルール保存"):
                with get_db() as conn:
                    conn.execute("UPDATE company_settings SET common_rule = ? WHERE id = 1", (co_v,))
                    conn.commit()
                st.success("保存したよ！")
        with t_sub[1]:
            with get_db() as conn:
                words = conn.execute("SELECT * FROM dictionary").fetchall()
            for w in words:
                st.write(f"📖 **{w['word']}**: {w['mean']}")
            nw, nm = st.text_input("用語", key="nw"), st.text_input("意味", key="nm")
            if st.button("辞書登録") and nw and nm:
                with get_db() as conn:
                    conn.execute("INSERT INTO dictionary (word, mean) VALUES (?, ?)", (nw, nm))
                    conn.commit()
                st.rerun()

    st.subheader("🆕 案件管理")
    with get_db() as conn:
        all_p = conn.execute("SELECT id, building_name, project_name FROM projects").fetchall()
   
    p_data = None
    if all_p:
        p_opts = {f"{r['building_name']} / {r['project_name']}": r['id'] for r in all_p}
        selected_label = st.selectbox("案件を選択", list(p_opts.keys()), key="p_select")
        p_id = p_opts[selected_label]
        with get_db() as conn:
            p_data = conn.execute("SELECT * FROM projects WHERE id = ?", (p_id,)).fetchone()
       
        br_v = st.text_area("🏙️ ビル固有ルール", p_data["building_rule"], height=80, key="br_input")
        pr_v = st.text_area("🚧 案件固有ルール", p_data["project_rule"], height=80, key="pr_input")
        if st.button("案件ルール保存"):
            with get_db() as conn:
                conn.execute("UPDATE projects SET building_rule=?, project_rule=? WHERE id=?", (br_v, pr_v, p_id))
                conn.commit()
            st.success("案件ルールを更新したよ！")

# --- 右カラム：メイン機能 ---
with col2:
    tabs = st.tabs(["📊 手順書解析", "📝 議事録比較", "💎 マスター・相談", "⚠️ 事故DB"])
  
    with tabs[0]:
        st.subheader("🔍 手順書チェック")
        m_mode = st.radio("解析方法", ["テキスト貼り付け", "PDF/画像アップ"], horizontal=True)
       
        # ルールの組み立て
        rule_context = f"【共通】: {cur_co}\n"
        if p_data:
            rule_context += f"【ビル】: {p_data['building_rule']}\n"
            rule_context += f"【案件】: {p_data['project_rule']}\n"
       
        sys_p = f"施工管理のプロとして手順書を解析せよ。以下のルールに基づきリスクを指摘しろ。\n{rule_context}"

        if m_mode == "テキスト貼り付け":
            txt_input = st.text_area("テキストを貼り付け", height=300, key="manual_text_input")
            if st.button("🚀 この内容を精査する"):
                if txt_input.strip():
                    with st.spinner("AIが考え中... しばらくお待ちください"):
                        # 直接 ask_ai を呼んで結果を表示
                        answer = ask_ai([
                            {"role": "system", "content": sys_p},
                            {"role": "user", "content": txt_input}
                        ])
                        st.markdown("### 📋 解析結果")
                        st.info(answer)
                else:
                    st.warning("テキストを入力してね")
        else:
            up = st.file_uploader("ファイルを選択", type=["pdf", "png", "jpg"])
            if up and st.button("🚀 ファイルを解析"):
                st.info("ファイル解析機能は現在テキスト版を優先して調整中です。")

    with tabs[1]:
        st.subheader("📝 議事録比較")
        st.write("前回と今回のPDFを比較して差分を出します（準備中）")

    with tabs[2]:
        st.subheader("💬 AI相談")
        m_content = p_data['master_content'] if p_data else ""
        st.write(f"**現在のマスター:** {m_content}")
        if prompt := st.chat_input("現場の相談をどうぞ"):
            st.write(ask_ai([{"role":"system","content":f"マスター:{m_content}"},{"role":"user","content":prompt}]))

    with tabs[3]:
        st.subheader("⚠️ 事故DB")
        with get_db() as conn:
            for r in conn.execute("SELECT * FROM accidents").fetchall():
                st.error(r['content'])
