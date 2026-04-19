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
# 🗄️ データベース管理（全テーブル復活）
# =====================
DB_PATH = "construction_ai.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# 初期化（欠けているテーブルも全て作成）
with get_db() as conn:
    conn.execute("""CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_name TEXT,
        building_name TEXT,
        building_rule TEXT DEFAULT '',
        project_rule TEXT DEFAULT '',
        master_content TEXT DEFAULT '',
        UNIQUE(project_name, building_name))""")
    conn.execute("CREATE TABLE IF NOT EXISTS company_settings (id INTEGER PRIMARY KEY, common_rule TEXT DEFAULT '')")
    conn.execute("INSERT OR IGNORE INTO company_settings (id, common_rule) VALUES (1, '')")
    conn.execute("CREATE TABLE IF NOT EXISTS dictionary (id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT, mean TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS accidents (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT)")
    conn.commit()

# =====================
# 👁️ 解析・AI関数
# =====================
def read_pdf_text(f):
    if not f: return ""
    r = PyPDF2.PdfReader(f)
    return "\n".join([p.extract_text() for p in r.pages])

def analyze_vision(image, sys_p):
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()
    res = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": sys_p},
                  {"role": "user", "content": [{"type":"text","text":"手順書を解析せよ"},{"type":"image_url","image_url":{"url":f"data:image/png;base64,{img_str}"}}]}],
        temperature=0.0
    )
    return res.choices[0].message.content

def ask_ai(messages):
    res = client.chat.completions.create(model="gpt-4o", messages=messages, temperature=0.1)
    return res.choices[0].message.content

# =====================
# UI構成
# =====================
st.title("🏗️ 施工管理AIツール (完全復旧版)")

# 共通ルールの取得
with get_db() as conn:
    c_set = conn.execute("SELECT common_rule FROM company_settings WHERE id = 1").fetchone()
    cur_co = c_set["common_rule"] if c_set else ""

col1, col2 = st.columns([1, 2])

# --- 左カラム：案件とルールの設定 ---
with col1:
    with st.expander("🏢 共通ルール・辞書"):
        tab_sub = st.tabs(["企業ルール", "辞書"])
        with tab_sub[0]:
            co_v = st.text_area("全案件共通", cur_co, height=100)
            if st.button("企業ルール保存"):
                with get_db() as conn:
                    conn.execute("UPDATE company_settings SET common_rule = ? WHERE id = 1", (co_v,))
                    conn.commit()
                st.rerun()
        with tab_sub[1]:
            with get_db() as conn:
                words = conn.execute("SELECT * FROM dictionary").fetchall()
            for w in words:
                c_d1, c_d2 = st.columns([4, 1])
                c_d1.write(f"📖 **{w['word']}**: {w['mean']}")
                if c_d2.button("🗑️", key=f"dict_{w['id']}"):
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
    with get_db() as conn:
        all_p = conn.execute("SELECT * FROM projects").fetchall()
   
    p_data = None
    if all_p:
        p_opts = {f"{r['building_name']} / {r['project_name']}": r['id'] for r in all_p}
        p_id = p_opts[st.selectbox("案件を選択", list(p_opts.keys()), key="main_p_select")]
        with get_db() as conn:
            p_data = conn.execute("SELECT * FROM projects WHERE id = ?", (p_id,)).fetchone()
       
        br_v = st.text_area("🏙️ ビル固有ルール", p_data["building_rule"], height=80, key="br_in")
        pr_v = st.text_area("🚧 案件固有ルール", p_data["project_rule"], height=80, key="pr_in")
        if st.button("案件ルール保存"):
            with get_db() as conn:
                conn.execute("UPDATE projects SET building_rule=?, project_rule=? WHERE id=?", (br_v, pr_v, p_id))
                conn.commit()
            st.success("保存完了")
   
    with st.expander("➕ 新規案件追加"):
        nb, np = st.text_input("ビル名"), st.text_input("案件名")
        if st.button("案件を登録"):
            with get_db() as conn:
                conn.execute("INSERT OR IGNORE INTO projects (building_name, project_name) VALUES (?, ?)", (nb, np))
                conn.commit()
            st.rerun()

# --- 右カラム：メイン機能 ---
with col2:
    tabs = st.tabs(["📊 手順書解析", "📝 議事録比較", "💎 マスター・相談", "⚠️ 事故DB"])
  
    # タブ1: 手順書解析（画像・PDF・テキスト対応）
    with tabs[0]:
        st.subheader("🔍 手査解析")
        m_mode = st.radio("方法", ["テキスト貼り付け", "PDF/画像アップ"], horizontal=True)
        sys_p = f"施工管理プロ。共通:{cur_co} / ビル:{p_data['building_rule'] if p_data else ''}"

        if m_mode == "PDF/画像アップ":
            up = st.file_uploader("ファイル", type=["pdf", "png", "jpg"])
            if up and st.button("🚀 精密解析"):
                if up.type == "application/pdf":
                    imgs = convert_from_bytes(up.read())
                    target = imgs[0]
                else: target = Image.open(up)
                st.image(target)
                st.info(analyze_vision(target, sys_p))
        else:
            txt = st.text_area("テキスト貼り付け", height=200)
            if st.button("🚀 テキスト精査"):
                st.info(ask_ai([{"role":"system","content":sys_p},{"role":"user","content":txt}]))

    # タブ2: 議事録比較（差分・チェックボックス復活）
    with tabs[1]:
        st.subheader("📝 議事録の差分をマスターへ")
        f1, f2 = st.file_uploader("前回", type="pdf"), st.file_uploader("今回", type="pdf")
        if f1 and f2 and st.button("🔄 比較"):
            t1, t2 = read_pdf_text(f1), read_pdf_text(f2)
            diff = ask_ai([{"role":"user", "content":f"新しい決定事項を抽出せよ。\n前回:{t1}\n今回:{t2}"}])
            st.session_state.diffs = [d.strip("- ") for d in diff.split("\n") if d.strip()]

        if "diffs" in st.session_state:
            sel = []
            for d in st.session_state.diffs:
                if st.checkbox(d, key=f"diff_{d}"): sel.append(d)
            if st.button("選択した項目をマスターに保存"):
                new_m = (p_data["master_content"] or "") + "\n" + "\n".join(sel)
                with get_db() as conn:
                    conn.execute("UPDATE projects SET master_content=? WHERE id=?", (new_m, p_id))
                    conn.commit()
                st.success("マスターを更新したよ！")

    # タブ3: マスター相談
    with tabs[2]:
        st.subheader("💬 AI相談 & マスター確認")
        st.write("### 現在のマスター内容")
        st.info(p_data["master_content"] if p_data else "未登録")
        if prompt := st.chat_input("相談..."):
            st.write(ask_ai([{"role":"system","content":f"マスター:{p_data['master_content']}"},{"role":"user","content":prompt}]))

    # タブ4: 事故DB
    with tabs[3]:
        st.subheader("⚠️ 事故DB")
        acc_in = st.text_input("事例追加")
        if st.button("登録"):
            with get_db() as conn:
                conn.execute("INSERT INTO accidents (content) VALUES (?)", (acc_in,))
                conn.commit()
            st.rerun()
        with get_db() as conn:
            for r in conn.execute("SELECT * FROM accidents").fetchall(): st.error(r['content'])
