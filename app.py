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
def read_pdf_text(f):
    if not f: return ""
    r = PyPDF2.PdfReader(f)
    return "\n".join([p.extract_text() for p in r.pages])

def analyze_with_vision(image, prompt):
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()
    res = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": [
                {"type": "text", "text": "手順書のリスクを精密に抽出してください。"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_str}"}}
            ]}
        ],
        temperature=0.0
    )
    return res.choices[0].message.content

def ask_ai(messages):
    try:
        res = client.chat.completions.create(model="gpt-4o", messages=messages, temperature=0.1)
        return res.choices[0].message.content
    except Exception as e:
        return f"AIエラーが発生しました: {str(e)}"

# =====================
# UI構成
# =====================
st.title("🏗️ 施工管理AIツール (案件連動修正版)")

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
            co_v = st.text_area("全案件共通", cur_co, height=80)
            if st.button("ルール保存"):
                with get_db() as conn:
                    conn.execute("UPDATE company_settings SET common_rule = ? WHERE id = 1", (co_v,))
                    conn.commit()
                st.rerun()
        with t_sub[1]:
            with get_db() as conn:
                words = conn.execute("SELECT * FROM dictionary").fetchall()
            for w in words:
                c_d1, c_d2 = st.columns([4, 1])
                c_d1.write(f"📖 **{w['word']}**: {w['mean']}")
                if c_d2.button("🗑️", key=f"del_{w['id']}"):
                    with get_db() as conn:
                        conn.execute("DELETE FROM dictionary WHERE id = ?", (w['id'],))
                        conn.commit()
                    st.rerun()
            nw, nm = st.text_input("用語"), st.text_input("意味")
            if st.button("辞書登録") and nw and nm:
                with get_db() as conn:
                    conn.execute("INSERT INTO dictionary (word, mean) VALUES (?, ?)", (nw, nm))
                    conn.commit()
                st.rerun()

    st.subheader("🆕 案件管理")
    with st.expander("➕ 新規案件を追加"):
        nb, np = st.text_input("ビル名"), st.text_input("案件名")
        if st.button("登録") and nb and np:
            with get_db() as conn:
                conn.execute("INSERT OR IGNORE INTO projects (building_name, project_name) VALUES (?, ?)", (nb, np))
                conn.commit()
            st.rerun()

    with get_db() as conn:
        all_p = conn.execute("SELECT id, building_name, project_name FROM projects").fetchall()
   
    # 案件選択と詳細データの取得
    p_data = None
    if all_p:
        p_opts = {f"{r['building_name']} / {r['project_name']}": r['id'] for r in all_p}
        selected_label = st.selectbox("案件を選択", list(p_opts.keys()))
        p_id = p_opts[selected_label]
        with get_db() as conn:
            p_data = conn.execute("SELECT * FROM projects WHERE id = ?", (p_id,)).fetchone()
       
        br_v = st.text_area("🏙️ ビル固有ルール", p_data["building_rule"], height=80)
        pr_v = st.text_area("🚧 案件固有ルール", p_data["project_rule"], height=80)
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
        m_mode = st.radio("解析方法", ["PDF/画像アップ", "テキスト貼り付け"], horizontal=True)
       
        # AIに渡すルールの組み立て（ここで案件データを確実に反映）
        rule_context = f"【共通ルール】: {cur_co}\n"
        if p_data:
            rule_context += f"【ビル固有ルール】: {p_data['building_rule']}\n"
            rule_context += f"【案件固有ルール】: {p_data['project_rule']}\n"
       
        sys_p = f"施工管理のプロとして手順書を解析せよ。以下のルールを厳守し、違反やリスクを指摘すること。\n{rule_context}"

        if m_mode == "PDF/画像アップ":
            up = st.file_uploader("ファイルを選択", type=["pdf", "png", "jpg", "jpeg"])
            if up:
                if up.type == "application/pdf":
                    try:
                        imgs = convert_from_bytes(up.read())
                        target = imgs[0]
                    except:
                        st.error("PDFの画像変換に失敗しました。テキスト貼り付けを試してください。")
                        st.stop()
                else: target = Image.open(up)
                st.image(target, use_container_width=True)
                if st.button("🚀 画像で解析開始"):
                    with st.status("AIが画像を隅々まで確認中..."):
                        res = analyze_with_vision(target, sys_p)
                        st.info(res)
        else:
            txt = st.text_area("テキストを貼り付け", height=300)
            if st.button("🚀 テキストで精査"):
                if txt.strip():
                    with st.status("AIが内容を精査中..."):
                        res = ask_ai([{"role":"system","content":sys_p},{"role":"user","content":txt}])
                        st.info(res)
                else: st.warning("精査するテキストを入力してね")

    with tabs[1]:
        st.subheader("📝 議事録比較")
        f1, f2 = st.file_uploader("前回議事録", type="pdf"), st.file_uploader("今回議事録", type="pdf")
        if f1 and f2 and st.button("🔄 差分を抽出"):
            t1, t2 = read_pdf_text(f1), read_pdf_text(f2)
            diff = ask_ai([{"role":"user", "content":f"新しい決定事項を箇条書きで出せ。\n前回:{t1}\n今回:{t2}"}])
            st.session_state.diffs = [d.strip("- ") for d in diff.split("\n") if d.strip()]

        if "diffs" in st.session_state:
            sel = []
            for d in st.session_state.diffs:
                if st.checkbox(d, key=d): sel.append(d)
            if st.button("選択した項目をマスターに登録") and p_data:
                new_m = (p_data["master_content"] or "") + "\n" + "\n".join(sel)
                with get_db() as conn:
                    conn.execute("UPDATE projects SET master_content=? WHERE id=?", (new_m, p_id))
                    conn.commit()
                st.success("マスターに登録したよ！")

    with tabs[2]:
        st.subheader("💬 自由相談 & マスター")
        current_master = p_data['master_content'] if p_data else "案件を選択してください"
        st.markdown(f"**現在の決定事項（マスター）:**\n{current_master}")
        if prompt := st.chat_input("現場の悩みやマスターについて相談してね"):
            st.write(ask_ai([{"role":"system","content":f"マスター情報:{current_master}"},{"role":"user","content":prompt}]))

    with tabs[3]:
        st.subheader("⚠️ 事故DB")
        acc_in = st.text_input("事故・ヒヤリハット事例を追加")
        if st.button("追加登録") and acc_in:
            with get_db() as conn:
                conn.execute("INSERT INTO accidents (content) VALUES (?)", (acc_in,))
                conn.commit()
            st.rerun()
        with get_db() as conn:
            for r in conn.execute("SELECT * FROM accidents").fetchall(): st.error(r['content'])
