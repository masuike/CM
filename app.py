import streamlit as st
import pandas as pd
import sqlite3
import os
import base64
from io import BytesIO
from openai import OpenAI
import PyPDF2
from pdf2image import convert_from_bytes
from PIL import Image

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
# 🛠️ 強化されたPDF読み取り（Vision & Text）
# =====================
def pdf_to_base64_images(pdf_bytes):
    """PDFを画像に変換してbase64文字列のリストを返す"""
    images = convert_from_bytes(pdf_bytes)
    base64_images = []
    for img in images:
        buffered = BytesIO()
        img.save(buffered, format="JPEG")
        base64_images.append(base64.b64encode(buffered.getvalue()).decode('utf-8'))
    return base64_images

def analyze_with_vision(pdf_bytes, system_prompt):
    """画像としてPDFを解析する（表形式に強い）"""
    base64_images = pdf_to_base64_images(pdf_bytes)
   
    # 最初の2ページ分を画像として送信（API制限とコスト考慮）
    content = [{"type": "text", "text": system_prompt}]
    for b64 in base64_images[:2]:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
        })

    try:
        res = client.chat.completions.create(
            model="gpt-4o", # Visionが使える最強モデル
            messages=[{"role": "user", "content": content}],
            max_tokens=2000
        )
        return res.choices[0].message.content
    except Exception as e:
        return f"AI解析エラー: {str(e)}"

# =====================
# UI
# =====================
st.title("🏗️ 施工管理AIツール (Vision強化版)")

col1, col2 = st.columns([1, 2])

# --- 共通データの取得 ---
with get_db() as conn:
    c_set = conn.execute("SELECT common_rule FROM company_settings WHERE id = 1").fetchone()
    current_co_rule = c_set["common_rule"] if c_set else ""
    acc_rows = conn.execute("SELECT * FROM accidents").fetchall()
    acc_context = "\n".join([f"・{r['content']}" for r in acc_rows])

# --- 左カラム（設定系） ---
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
  
    project_folder = ""
    sel_label = ""
    p_data = None
    if all_p:
        p_options = {f"{r['building_name']} / {r['project_name']}": r['id'] for r in all_p}
        sel_label = st.selectbox("案件を選んでね", list(p_options.keys()))
        p_id = p_options[sel_label]
        with get_db() as conn:
            p_data = conn.execute("SELECT * FROM projects WHERE id = ?", (p_id,)).fetchone()
        project_folder = f"{p_data['building_name']}_{p_data['project_name']}"
        br_v = st.text_area("🏙️ ビル固有ルール", p_data["building_rule"], key=f"br_{p_id}", height=100)
        pr_v = st.text_area("🚧 案件固有ルール", p_data["project_rule"], key=f"pr_{p_id}", height=100)
        if st.button("案件ルール保存"):
            with get_db() as conn:
                conn.execute("UPDATE projects SET building_rule=?, project_rule=? WHERE id=?", (br_v, pr_v, p_id))
                conn.commit()
            st.success("保存完了")

# --- 右カラム（メイン機能） ---
with col2:
    tabs = st.tabs(["📊 資料解析チャット", "🔄 差分抽出", "💎 マスター", "⚠️ 事故DB"])
  
    with tabs[0]:
        st.subheader(f"💬 {sel_label} の相談窓口")
       
        with st.expander("📁 PDF手順書をアップロード（画像として精密解析）", expanded=True):
            up_file = st.file_uploader("PDFを選択", type="pdf", key="pro_up")
            if up_file and st.button("🚀 画像解析を実行"):
                pdf_bytes = up_file.read()
               
                with st.status("AIが資料を「画像」として隅々まで確認中..."):
                    # --- 現場監督の「眼」を持つプロンプト ---
                    sys_p = f"""あなたは30年の経験を持つ超ベテランの施工管理技士です。
送られた画像（PDF）の「表」を隅々まで見て、以下の情報を精査してください。

【1. 基本情報の確認】
・開始時間は何時か？（表の隅まで確認してください）
・緊急連絡先（AE社、電力会社等）の電話番号は記載されているか？

【2. 技術的リスクの深掘り】
・「分電盤OFF」がある場合、負荷側（PC等）への影響周知があるか？
・作業のステップに漏れはないか？（例：WhM交換が別日の場合、手順が混ざっていないか）

【3. ルール照合】
・企業共通ルール: {current_co_rule}
・ビル・案件ルール: {p_data['building_rule'] if p_data else ''} / {p_data['project_rule'] if p_data else ''}
・これらと照らして、場所（階数）や手順に矛盾がないか？

【4. 指摘事項】
現場監督として「これじゃ承認できない」という不備を【⚠️警告】として、
プロとしてのアドバイスを【🔍技術的注意点】として出力してください。
"""
                    st.session_state.auto_analysis = analyze_with_vision(pdf_bytes, sys_p)
                st.success("画像解析が完了しました！")

        if "auto_analysis" in st.session_state:
            st.info("💡 **AIによる精密解析結果（表も読めています）**")
            st.markdown(st.session_state.auto_analysis)
            if st.button("解析結果をクリア"):
                del st.session_state.auto_analysis
                st.rerun()

    with tabs[1]:
        st.subheader("新旧比較")
        st.write("※ここは文字ベースの比較です")
        # （以前のコードと同様の比較処理をここに追加可能）

    with tabs[2]:
        if p_data:
            m_val = st.text_area("決定事項", p_data["master_content"], height=300)
            if st.button("マスター保存"):
                with get_db() as conn:
                    conn.execute("UPDATE projects SET master_content=? WHERE id=?", (m_val, p_id))
                    conn.commit()
                st.success("保存したよ")

    with tabs[3]:
        st.subheader("⚠️ 事故DB")
        for r in acc_rows: st.info(r['content'])
