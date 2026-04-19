import streamlit as st
import os
from openai import OpenAI
from PyPDF2 import PdfReader

# --- 🔐 初期設定 ---
# パスワード（以前と同じ test123）
PASSWORD = os.getenv("APP_PASSWORD", "test123")
# OpenAIの接続準備
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
# 資料が入っているフォルダ名
DATA_DIR = "data"

# ログイン状態の確認
if "auth" not in st.session_state:
    st.session_state.auth = False

# --- 1. ログイン画面 ---
if not st.session_state.auth:
    st.title("🔒 施工管理AIログイン")
    pw = st.text_input("パスワードを入力してください", type="password")
    if st.button("ログイン"):
        if pw == PASSWORD:
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("パスワードが違います")
    st.stop()

# --- 2. メイン機能（ログイン後のみ表示） ---
st.title("🏗️ 施工管理AIアシスタント")
st.caption("アップロードされた議事録や資料から回答します")

# PDFから文字を読み取る関数（おまじない）
def get_pdf_text(path):
    text = ""
    try:
        reader = PdfReader(path)
        for page in reader.pages:
            content = page.extract_text()
            if content:
                text += content
    except Exception as e:
        return f"エラー: {e}"
    return text

# 「data」フォルダ内のすべてのPDFをスキャンして中身を合体させる
all_text = ""
if os.path.exists(DATA_DIR):
    for root, dirs, files in os.walk(DATA_DIR):
        for file in files:
            if file.endswith(".pdf"):
                all_text += f"\n--- ファイル名: {file} ---\n"
                all_text += get_pdf_text(os.path.join(root, file))

# チャット履歴の表示
if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# 質問の入力
if prompt := st.chat_input("この現場について質問してください"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        # AIに資料を渡して、「これに基づいて答えて」と命令する
        system_msg = f"あなたは優秀な施工管理技士です。以下の資料に基づいて回答してください。資料にないことは『分かりません』と答えてください。\n\n資料:\n{all_text}"
       
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_msg},
                *[{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]
            ]
        )
        answer = response.choices[0].message.content
        st.markdown(answer)
   
    st.session_state.messages.append({"role": "assistant", "content": answer})

# ログアウトボタン（サイドバー）
if st.sidebar.button("ログアウト"):
    st.session_state.auth = False
    st.rerun()
