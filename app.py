import streamlit as st
import pandas as pd
import sqlite3
import os
import base64
from io import BytesIO
from openai import OpenAI
from PIL import Image
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
        conn.execute("CREATE TABLE IF NOT EXISTS accidents (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS dictionary (id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT, mean TEXT)")
        conn.commit()

init_db()

# =====================
# 🛠️ 便利関数
# =====================
def read_pdf(f):
    if not f: return ""
    r = PyPDF2.PdfReader(f)
    return "\n".join([p.extract_text() for p in r.pages])

def ask_ai(messages):
    res = client.chat.completions.create(model="gpt-4o", messages=messages, temperature=0.1)
    return res.choices[0].message.content

# =====================
# UI構成
# =====================
st.title("🏗️ 施工管理AIツール (全機能・辞書復活版)")

col1, col2 = st.columns([1, 2])

# --- 左カラム：案件と設定 ---
with col1:
    with st.expander("🏢 共通ルール・辞書"):
        tab_sub = st.tabs(["ルール", "辞書"])
        with tab_sub[0]:
            with get_db() as conn:
                c_set = conn.execute("SELECT common_rule FROM company_settings WHERE id = 1").fetchone()
                current_co_rule = c_set["common_rule"] if c_set else ""
            co_v = st.text_area("全案件共通", current_co_rule, height=80)
            if st.button("企業ルール保存"):
                with get_db() as conn:
                    conn.execute("UPDATE company_settings SET common_rule = ? WHERE id = 1", (co_v,))
                    conn.commit()
                st.success("保存完了")
        with tab_sub[1]:
            with get_db() as conn:
                words = conn.execute("SELECT * FROM dictionary").fetchall()
            for w in words: st.write(f"📖 **{w['word']}**: {w['mean']}")
            new_w = st.text_input("用語")
            new_m = st.text_input("意味")
            if st.button("辞書登録"):
                with get_db() as conn:
                    conn.execute("INSERT INTO dictionary (word, mean) VALUES (?, ?)", (new_w, new_m))
                    conn.commit()
                st.rerun()

    st.subheader("🆕 案件管理")
    with st.expander("➕ 新規案件を追加"):
        nb = st.text_input("ビル名")
        np = st.text_input("案件名")
        if st.button("案件を登録"):
            with get_db() as conn:
                conn.execute("INSERT OR IGNORE INTO projects (building_name, project_name) VALUES (?, ?)", (nb, np))
                conn.commit()
            st.rerun()

    with get_db() as conn:
        all_p = conn.execute("SELECT id, building_name, project_name FROM projects").fetchall()
   
    p_data = None
    if all_p:
        p_options = {f"{r['building_name']} / {r['project_name']}": r['id'] for r in all_p}
        sel_label = st.selectbox("案件を選択", list(p_options.keys()))
        p_id = p_options[sel_label]
        with get_db() as conn:
            p_data = conn.execute("SELECT * FROM projects WHERE id = ?", (p_id,)).fetchone()
       
        br_v = st.text_area("🏙️ ビル固有ルール", p_data["building_rule"], height=100)
        pr_v = st.text_area("🚧 案件固有ルール", p_data["project_rule"], height=100)
        if st.button("案件ルールを保存"):
            with get_db() as conn:
                conn.execute("UPDATE projects SET building_rule=?, project_rule=? WHERE id=?", (br_v, pr_v, p_id))
                conn.commit()
            st.success("保存完了")

# --- 右カラム：メイン機能 ---
with col2:
    tabs = st.tabs(["📊 手順書精査", "📝 議事録比較", "💬 自由相談", "⚠️ 事故DB"])
  
    # --- タブ1: 手順書精査 ---
    with tabs[0]:
        st.subheader("🔍 手順書テキスト解析")
        manual_text = st.text_area("手順書の中身をコピペしてね", height=300)
        if st.button("🚀 プロの視点でチェック"):
            sys_p = f"施工管理のプロとして以下をチェック：緊急連絡先、作業時間、分電盤OFFの影響。ルール：{current_co_rule} / {p_data['building_rule'] if p_data else ''}"
            ans = ask_ai([{"role":"system", "content":sys_p}, {"role":"user", "content":manual_text}])
            st.info(ans)

    # --- タブ2: 議事録比較 ---
    with tabs[1]:
        st.subheader("📂 議事録比較・マスター登録")
        f1 = st.file_uploader("前回議事録", type="pdf")
        f2 = st.file_uploader("今回議事録", type="pdf")
        if f1 and f2 and st.button("🔄 差分を抽出"):
            t1, t2 = read_pdf(f1), read_pdf(f2)
            prompt = f"前回と今回の議事録を比較し、新しく決まった事項や変更点を「箇条書き」で抽出してください。\n前回：{t1}\n今回：{t2}"
            diff = ask_ai([{"role":"user", "content":prompt}])
            st.session_state.diff_list = [d.strip("- ") for d in diff.split("\n") if d.strip()]

        if "diff_list" in st.session_state:
            st.write("### 💎 マスターに登録する項目を選んでね")
            selected = []
            for d in st.session_state.diff_list:
                if st.checkbox(d, key=d): selected.append(d)
            if st.button("選択した項目をマスターに保存"):
                new_master = p_data["master_content"] + "\n" + "\n".join(selected)
                with get_db() as conn:
                    conn.execute("UPDATE projects SET master_content=? WHERE id=?", (new_master, p_id))
                    conn.commit()
                st.success("マスターを更新しました！")

    # --- タブ3: 自由相談 ---
    with tabs[2]:
        st.subheader("💬 AI相談 & マスター確認")
        st.write("### 現在のマスター内容")
        st.info(p_data["master_content"] if p_data else "未登録")
        user_in = st.chat_input("何でも聞いてね")
        if user_in:
            res = ask_ai([{"role":"system", "content":f"マスター情報：{p_data['master_content'] if p_data else ''}"}, {"role":"user", "content":user_in}])
            st.write(res)

    # --- タブ4: 事故DB ---
    with tabs[3]:
        st.subheader("⚠️ 事故DB")
        new_acc = st.text_input("事故・ヒヤリハット追加")
        if st.button("DB登録"):
            with get_db() as conn:
                conn.execute("INSERT INTO accidents (content) VALUES (?)", (new_acc,))
                conn.commit()
            st.rerun()
        with get_db() as conn:
            for r in conn.execute("SELECT * FROM accidents").fetchall(): st.error(r['content'])
