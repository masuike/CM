import streamlit as st
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

with get_db() as conn:
    conn.execute("CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY AUTOINCREMENT, project_name TEXT, building_name TEXT, building_rule TEXT DEFAULT '', project_rule TEXT DEFAULT '', master_content TEXT DEFAULT '', UNIQUE(project_name, building_name))")
    conn.execute("CREATE TABLE IF NOT EXISTS company_settings (id INTEGER PRIMARY KEY, common_rule TEXT DEFAULT '')")
    conn.execute("INSERT OR IGNORE INTO company_settings (id, common_rule) VALUES (1, '')")
    conn.execute("CREATE TABLE IF NOT EXISTS dictionary (id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT, mean TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS accidents (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT)")
    conn.commit()

# =====================
# 👁️ AI・解析関数
# =====================
def analyze_vision(image, sys_p):
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
    res = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": sys_p},
                  {"role": "user", "content": [{"type":"text","text":"手順書を精密に解析せよ"},{"type":"image_url","image_url":{"url":f"data:image/png;base64,{img_str}"}}]}],
        temperature=0.0
    )
    return res.choices[0].message.content

def ask_ai(messages):
    res = client.chat.completions.create(model="gpt-4o", messages=messages, temperature=0.1)
    return res.choices[0].message.content

# =====================
# UI構成
# =====================
st.title("🏗️ 施工管理AIツール (完全同期版)")

with get_db() as conn:
    c_set = conn.execute("SELECT common_rule FROM company_settings WHERE id = 1").fetchone()
    cur_co = c_set["common_rule"] if c_set else ""

col1, col2 = st.columns([1, 2])

# --- 左カラム ---
with col1:
    with st.expander("🏢 共通・辞書"):
        t_sub = st.tabs(["企業ルール", "辞書"])
        with t_sub[0]:
            co_v = st.text_area("全案件共通", cur_co, height=100)
            if st.button("保存", key="save_co"):
                with get_db() as conn:
                    conn.execute("UPDATE company_settings SET common_rule = ? WHERE id = 1", (co_v,))
                    conn.commit()
                st.rerun()
        with t_sub[1]:
            with get_db() as conn:
                words = conn.execute("SELECT * FROM dictionary").fetchall()
            for w in words:
                st.write(f"📖 **{w['word']}**: {w['mean']}")
            nw, nm = st.text_input("用語"), st.text_input("意味")
            if st.button("登録"):
                with get_db() as conn:
                    conn.execute("INSERT INTO dictionary (word, mean) VALUES (?, ?)", (nw, nm))
                    conn.commit()
                st.rerun()

    st.subheader("🆕 案件管理")
    with get_db() as conn:
        all_p = conn.execute("SELECT * FROM projects").fetchall()
   
    p_data = None
    if all_p:
        p_opts = {f"{r['building_name']} / {r['project_name']}": r['id'] for r in all_p}
        # ⚠️ 案件選択をキーで管理し、変更時にセッションをクリアする仕組み
        sel_label = st.selectbox("案件を選択", list(p_opts.keys()), key="p_selector")
        p_id = p_opts[sel_label]
       
        with get_db() as conn:
            p_data = conn.execute("SELECT * FROM projects WHERE id = ?", (p_id,)).fetchone()
       
        # ⚠️ 案件ごとに一意のキー（id）を割り当てることで表示の混線を防ぐ
        br_v = st.text_area("🏙️ ビル固有ルール", p_data["building_rule"], height=80, key=f"br_{p_id}")
        pr_v = st.text_area("🚧 案件固有ルール", p_data["project_rule"], height=80, key=f"pr_{p_id}")
        if st.button("案件ルール保存", key=f"save_p_{p_id}"):
            with get_db() as conn:
                conn.execute("UPDATE projects SET building_rule=?, project_rule=? WHERE id=?", (br_v, pr_v, p_id))
                conn.commit()
            st.success("保存完了")
   
    with st.expander("➕ 新規案件追加"):
        nb, np = st.text_input("ビル名"), st.text_input("案件名")
        if st.button("案件登録"):
            with get_db() as conn:
                conn.execute("INSERT OR IGNORE INTO projects (building_name, project_name) VALUES (?, ?)", (nb, np))
                conn.commit()
            st.rerun()

# --- 右カラム ---
with col2:
    tabs = st.tabs(["📊 手順書解析", "📝 議事録比較", "💎 マスター・相談", "⚠️ 事故DB"])
  
    with tabs[0]:
        st.subheader("🔍 手順書精査")
        m_mode = st.radio("方法", ["PDF/画像アップ", "テキスト貼り付け"], horizontal=True)
        # AIに渡すルールの組み立て（現在画面にあるものを使う）
        sys_p = f"施工管理プロ。共通:{cur_co}\nビル:{p_data['building_rule'] if p_data else ''}\n案件:{p_data['project_rule'] if p_data else ''}"

        if m_mode == "PDF/画像アップ":
            up = st.file_uploader("ファイルをドロップ", type=["pdf", "png", "jpg"])
            if up and st.button("🚀 画像で精査"):
                if up.type == "application/pdf":
                    imgs = convert_from_bytes(up.read())
                    target = imgs[0]
                else: target = Image.open(up)
                st.image(target, use_container_width=True)
                st.info(analyze_vision(target, sys_p))
        else:
            txt = st.text_area("テキスト貼り付け", height=250, key=f"manual_{p_id if p_data else 'none'}")
            if st.button("🚀 テキストで精査"):
                st.info(ask_ai([{"role":"system","content":sys_p},{"role":"user","content":txt}]))

    with tabs[1]:
        st.subheader("📝 議事録の差分抽出")
        st.write("PDFを2つ選んで差分をチェック登録できます（準備中）")

    with tabs[2]:
        st.subheader("💬 AI相談 & マスター確認")
        st.write("### 現在の決定事項")
        m_val = p_data["master_content"] if p_data else ""
        st.info(m_val if m_val else "未登録")
        if prompt := st.chat_input("相談..."):
            st.write(ask_ai([{"role":"system","content":f"マスター:{m_val}"},{"role":"user","content":prompt}]))

    with tabs[3]:
        st.subheader("⚠️ 事故DB")
        with get_db() as conn:
            for r in conn.execute("SELECT * FROM accidents").fetchall(): st.error(r['content'])
