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
# 🛠️ 強化されたPDF読み取り（Vision集中型）
# =====================
def pdf_to_base64_images(pdf_bytes):
    """PDFを画像に変換してbase64文字列のリストを返す"""
    # 混乱を防ぐため解像度を高めに設定(dpi=200)
    images = convert_from_bytes(pdf_bytes, dpi=200)
    base64_images = []
    for img in images:
        buffered = BytesIO()
        img.save(buffered, format="JPEG")
        base64_images.append(base64.b64encode(buffered.getvalue()).decode('utf-8'))
    return base64_images

def analyze_with_vision(pdf_bytes, system_prompt):
    """画像としてPDFの1ページ目に全集中して解析する"""
    base64_images = pdf_to_base64_images(pdf_bytes)
   
    if not base64_images:
        return "PDFの画像変換に失敗しました。"

    # AIへの指示を強化：表の1文字1文字を読み取るように命令
    instruction = (
        "添付された画像は工事の施工手順書です。表の構造（行と列の関係）を正確に把握し、"
        "特に「開始時間」「作業場所」「緊急連絡先（電話番号）」「作業ステップの詳細」を"
        "一文字も漏らさずに読み取ってください。その内容を踏まえて、以下の指示を実行してください。\n\n"
        + system_prompt
    )
   
    content = [
        {"type": "text", "text": instruction}
    ]
   
    # 1ページ目に全神経を集中させる（混乱防止）
    content.append({
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{base64_images[0]}"}
    })

    try:
        res = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": content}],
            max_tokens=2000,
            temperature=0.0 # 数値や事実の正確性を最大化
        )
        return res.choices[0].message.content
    except Exception as e:
        return f"AI解析エラー: {str(e)}"

# =====================
# UI
# =====================
st.title("🏗️ 施工管理AIツール (Vision全集中版)")

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
       
        with st.expander("📁 PDF手順書を精密解析（1枚目に全集中）", expanded=True):
            up_file = st.file_uploader("PDFを選択", type="pdf", key="pro_up")
            if up_file and st.button("🚀 精密解析を実行"):
                pdf_bytes = up_file.read()
               
                with st.status("ベテラン監督の眼で表を読み取り中..."):
                    # --- 指示内容の定義 ---
                    sys_p = f"""【解析の柱】
1. 資料内の「開始時間」と「緊急連絡先（AE社等）」の有無を明示せよ。
2. 施工場所（階数）を特定し、関係ないルールは完全に無視せよ。
3. 「分電盤OFF」に伴う、負荷側への停電周知やサーバー等への影響確認の有無を厳しく指摘せよ。
4. WhM交換などの主要作業が、他の日と混同されていないか整合性を確認せよ。

【参照ルール】
・企業共通: {current_co_rule}
・案件/ビル: {p_data['building_rule'] if p_data else ''} / {p_data['project_rule'] if p_data else ''}

【出力】
不備は【⚠️警告】、プロの視点は【🔍技術的注意点】として出力してください。
"""
                    st.session_state.auto_analysis = analyze_with_vision(pdf_bytes, sys_p)
                st.success("精密解析が完了しました！")

        if "auto_analysis" in st.session_state:
            st.info("💡 **AIによる精密解析結果**")
            st.markdown(st.session_state.auto_analysis)
            if st.button("表示をクリア"):
                del st.session_state.auto_analysis
                st.rerun()
            st.write("---")

        if "chat_history" not in st.session_state: st.session_state.chat_history = []
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
       
        if user_query := st.chat_input("もっと詳しく聞きたいことは？"):
            st.session_state.chat_history.append({"role": "user", "content": user_query})
            with st.chat_message("user"): st.markdown(user_query)
            with st.chat_message("assistant"):
                sys_p = f"施工プロとして回答せよ。共通ルール:{current_co_rule}\n事故DB:{acc_context}"
                ans = ask_ai([{"role":"system", "content":sys_p}] + st.session_state.chat_history)
                st.markdown(ans)
                st.session_state.chat_history.append({"role": "assistant", "content": ans})

    with tabs[1]:
        st.subheader("新旧比較（テキストベース）")
        st.write("※図面や表の視覚的な比較は今後実装予定です")

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
