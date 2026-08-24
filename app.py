import streamlit as st
import requests
import re
import time
import threading
import queue
import io
import zipfile
import json
import concurrent.futures
from datetime import datetime
from bs4 import BeautifulSoup
from supabase import create_client, Client, ClientOptions
from google import genai
from google.genai import types

# ==========================================
# 1. CẤU HÌNH TRANG VÀ KẾT NỐI SUPABASE
# ==========================================
st.set_page_config(page_title="Máy Dịch Truyện - Tối Giản", page_icon="⚡", layout="wide")

SUPABASE_URL = ""
SUPABASE_KEY = ""
try:
    if "SUPABASE_URL" in st.secrets: SUPABASE_URL = st.secrets["SUPABASE_URL"]
    if "SUPABASE_KEY" in st.secrets: SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
except Exception: pass

@st.cache_resource
def init_supabase():
    if SUPABASE_URL and SUPABASE_KEY:
        try: 
            opts = ClientOptions(postgrest_client_timeout=60, storage_client_timeout=60)
            return create_client(SUPABASE_URL, SUPABASE_KEY, options=opts)
        except Exception: return None
    return None

supabase = init_supabase()

# Regex xử lý sạch khoảng trắng tiếng Trung (\s, \u3000, space) và nhận diện đa định dạng
DEFAULT_SPLIT_REGEX = r'(?m)^[\s\u3000]*(?:第\s*[0-9一二三四五六七八九十百千万零]+\s*[章回节集卷部].*|[0-9]{1,5}\s*[、.．:：\s-]\s*\S.*|【[^】\n]+】|(?:Chapter|Chương)\s*[0-9]+.*)'

if "authenticated" not in st.session_state: st.session_state.authenticated = False
if "user_email" not in st.session_state: st.session_state.user_email = ""
if "trans_status" not in st.session_state: st.session_state.trans_status = {}
if "novel_data" not in st.session_state:
    st.session_state.novel_data = {
        "api_keys": {"gemini": ""},
        "selected_model": "Gemini 3.7 Flash (Mới nhất, Siêu tốc)",
        "raw_docs": [],
        "raw_chapters": {},
        "trans_prompt": "Bạn là một dịch giả tiểu thuyết chuyên nghiệp. Dịch mượt mà, thuần Việt, giữ nguyên đoạn văn và không tự ý thêm bớt tình tiết."
    }

if "worker_running" not in st.session_state:
    st.session_state.worker_running = False

# ==========================================
# 2. HÀM TÁCH CHƯƠNG THÔNG MINH
# ==========================================
def smart_split_novel(raw_text: str, custom_regex: str = "") -> list[tuple[str, str]]:
    reg = custom_regex.strip() if custom_regex and custom_regex.strip() else DEFAULT_SPLIT_REGEX
    pattern = re.compile(reg, re.MULTILINE)
    
    matches = list(pattern.finditer(raw_text))
    chapters = []
    
    if not matches:
        return []
    
    # Bỏ qua phần rác đầu dòng nếu chỉ toàn khoảng trắng hoặc quá ngắn
    if matches[0].start() > 0:
        intro_content = raw_text[:matches[0].start()].strip()
        # Chỉ tạo phần mở đầu nếu nội dung chứa trên 50 ký tự chữ thực
        if len(re.sub(r'[\s\u3000\r\n]+', '', intro_content)) > 50:
            chapters.append(("Phần Mở Đầu / Tiền Truyện", intro_content))
            
    for i, match in enumerate(matches):
        title = match.group(0).strip()
        start_idx = match.end()
        end_idx = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)
        content = raw_text[start_idx:end_idx].strip()
        
        safe_title = re.sub(r'[\r\n\u3000\t]+', ' ', title)[:50].strip()
        if content:
            chapters.append((safe_title, content))
            
    return chapters

# ==========================================
# 3. HÀM HỖ TRỢ & CALL API
# ==========================================
def load_user_data_from_supabase(email):
    if supabase:
        try:
            res = supabase.table("workspaces").select("workspace_data").eq("email", email).execute()
            if res.data and len(res.data) > 0:
                saved_data = res.data[0].get("workspace_data")
                st.session_state.novel_data.update(saved_data)
                st.toast("🎉 Đã tải dữ liệu trên mây!", icon="✅")
        except Exception as e: 
            st.error(f"Lỗi tải dữ liệu: {e}")

def save_user_data_to_supabase():
    if supabase and st.session_state.authenticated and st.session_state.user_email:
        try:
            supabase.table("workspaces").upsert({"email": st.session_state.user_email, "workspace_data": st.session_state.novel_data}).execute()
            st.toast("💾 Đã lưu dữ liệu tự động!", icon="☁️")
        except Exception as e: 
            st.error(f"Lỗi lưu Supabase: {e}")

def reset_all_chapters():
    """Hàm dọn sạch toàn bộ hàng chờ dịch trên cả Session và Supabase Database"""
    st.session_state.novel_data["raw_chapters"] = {}
    st.session_state.trans_status = {}
    if supabase and st.session_state.user_email:
        try:
            supabase.table("workspaces").upsert({
                "email": st.session_state.user_email, 
                "workspace_data": st.session_state.novel_data
            }).execute()
        except Exception:
            pass

# --- CÁC HÀM XỬ LÝ ZHIHU ---
def parse_zhihu_content(soup):
    texts = []
    script_tag = soup.find('script', id='js-initialData')
    if script_tag and script_tag.string:
        try:
            data = json.loads(script_tag.string)
            initial_state = data.get('initialState', {})
            entities = initial_state.get('entities', {})
            articles = entities.get('articles', {})
            for item_id, item_data in articles.items():
                if 'content' in item_data:
                    c_soup = BeautifulSoup(item_data['content'], 'html.parser')
                    texts.append(c_soup.get_text(separator="\n", strip=True))
                    
            if not texts:
                str_data = json.dumps(initial_state, ensure_ascii=False)
                found_contents = re.findall(r'"content"\s*:\s*"([^"]+)"', str_data)
                for fc in found_contents:
                    if len(fc) > 200:
                        c_soup = BeautifulSoup(fc.encode().decode('unicode-escape', errors='ignore'), 'html.parser')
                        texts.append(c_soup.get_text(separator="\n", strip=True))
        except Exception: pass

    if not texts:
        content_nodes = soup.find_all(['div', 'section', 'article'], class_=re.compile(r'(Post-RichText|BodyModule|css-1y8291e|PaidColumn)', re.IGNORECASE))
        for node in content_nodes:
            txt = node.get_text(separator="\n", strip=True)
            if len(txt) > 100: texts.append(txt)

    if not texts:
        ps = soup.find_all('p')
        if len(ps) > 5: texts = [p.get_text().strip() for p in ps if p.get_text().strip()]

    return "\n\n".join(texts) if texts else ""

def scrape_zhihu_url(url, custom_cookie=""):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.7',
        }
        cookie_val = custom_cookie.strip()
        if cookie_val:
            if cookie_val.startswith('[') and cookie_val.endswith(']'):
                try:
                    cookie_list = json.loads(cookie_val)
                    cookie_val = "; ".join([f"{c['name']}={c['value']}" for c in cookie_list if 'name' in c and 'value' in c])
                except Exception: pass
            headers['Cookie'] = cookie_val

        res = requests.get(url, headers=headers, timeout=15)
        res.encoding = res.apparent_encoding
        res.raise_for_status() 
        soup = BeautifulSoup(res.text, 'html.parser')
        text = parse_zhihu_content(soup)
        return text if len(text) >= 50 else None, None
    except Exception as e: 
        return None, str(e)

# --- HÀM CÀO WEB CHUNG ---
def scrape_web_chapter(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        res = requests.get(url.strip(), headers=headers, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        
        title_tag = soup.find('h1')
        title = title_tag.get_text().strip() if title_tag else ""
        if not title and soup.title: title = soup.title.string.strip()
        if not title: title = "Chương Web Mới"

        content_div = soup.select_one('#chapter-c, .chapter-content, #chapter-content, .box-chap, .story-detail-content')
        if content_div:
            paragraphs = content_div.find_all('p')
            if paragraphs: text = "\n".join([p.get_text().strip() for p in paragraphs if p.get_text().strip()])
            else: text = content_div.get_text(separator="\n", strip=True)
        else:
            paragraphs = soup.find_all('p')
            if paragraphs and len(paragraphs) > 5: text = "\n".join([p.get_text().strip() for p in paragraphs if p.get_text().strip()])
            else: text = soup.get_text(separator="\n", strip=True)
                
        return title, text if len(text) > 50 else "Không tìm thấy nội dung truyện ở link này."
    except Exception as e: 
        return "Lỗi", f"❌ Lỗi cào web: {str(e)}"

# --- HÀM GỌI LLM ---
def call_llm(system_prompt, prompt_text, api_keys, model_choice) -> tuple[bool, str]:
    gemini_keys = [k.strip() for k in re.split(r'[\n,;\s]+', api_keys.get("gemini", "")) if k.strip()]
    if not gemini_keys: 
        return False, "Chưa nhập Gemini API Key."
    
    if "3.7 Flash" in str(model_choice): model_name = "gemini-3.7-flash"
    elif "3.6 Flash" in str(model_choice): model_name = "gemini-3.6-flash"
    elif "3.5 Flash" in str(model_choice): model_name = "gemini-3.5-flash"
    elif "3.1 Flash-Lite" in str(model_choice): model_name = "gemini-3.1-flash-lite"
    elif "2.5 Pro" in str(model_choice): model_name = "gemini-2.5-pro"
    elif "2.5 Flash" in str(model_choice): model_name = "gemini-2.5-flash"
    else: model_name = "gemini-3.7-flash" 

    num_keys = len(gemini_keys)
    last_error = ""
    
    for i in range(num_keys):
        current_key = gemini_keys[i]
        try: 
            client = genai.Client(api_key=current_key)
            safety_settings = [
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            ]
            
            config = types.GenerateContentConfig(
                system_instruction=system_prompt if system_prompt else None,
                safety_settings=safety_settings,
                temperature=0.3
            )
            
            res = client.models.generate_content(model=model_name, contents=prompt_text, config=config)
            if res and res.text:
                return True, res.text
            else:
                last_error = "AI trả về kết quả rỗng."
        except Exception as e:
            last_error = str(e)
            continue 
            
    return False, f"Lỗi API: {last_error}"

def process_single_chapter(chap_key, raw_text, api_keys, model_choice, novel_data_dict, trans_status_dict, custom_prompt=None):
    max_retries = 3
    retry_count = 0
    base_prompt = custom_prompt if custom_prompt else novel_data_dict.get("trans_prompt", "Bạn là dịch giả.")
    system_prompt = base_prompt + "\n\n[LỆNH BẮT BUỘC]: Trả về trực tiếp bản dịch tiếng Việt mượt mà. Không giải thích thêm."
    
    while retry_count < max_retries:
        success, result = call_llm(system_prompt, f"RAW CẦN DỊCH:\n\n{raw_text}", api_keys, model_choice)
        if success:
            novel_data_dict["raw_chapters"][chap_key]["translated"] = result
            trans_status_dict[chap_key] = "✅ Hoàn thành"
            break
        else:
            retry_count += 1
            if "429" in result or "quota" in result.lower() or "exhausted" in result.lower():
                trans_status_dict[chap_key] = f"⚠️ Quá tải API (Chờ 30s thử lại lần {retry_count}/{max_retries})"
                time.sleep(30)
            else:
                novel_data_dict["raw_chapters"][chap_key]["translated"] = f"❌ Lỗi: {result}"
                trans_status_dict[chap_key] = "❌ Lỗi Hệ Thống"
                break
                
    if retry_count >= max_retries:
        trans_status_dict[chap_key] = "❌ Thất bại (Hết Quota)"

def batch_worker(chap_keys_list, api_keys, model_choice, novel_data_dict, trans_status_dict, delay_time, user_email):
    st.session_state.worker_running = True
    batch_size = 3  
    
    keys_to_translate = [
        k for k in chap_keys_list 
        if not novel_data_dict["raw_chapters"][k].get("translated") or "❌" in novel_data_dict["raw_chapters"][k].get("translated", "")
    ]

    for i in range(0, len(keys_to_translate), batch_size):
        batch = keys_to_translate[i : i + batch_size]
        for k in batch:
            trans_status_dict[k] = "🔄 Đang dịch..."
            
        with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as executor:
            futures = [
                executor.submit(
                    process_single_chapter, 
                    k, 
                    novel_data_dict["raw_chapters"][k]["raw"], 
                    api_keys, 
                    model_choice, 
                    novel_data_dict, 
                    trans_status_dict
                ) for k in batch
            ]
            concurrent.futures.wait(futures)
            
        if supabase and user_email:
            try:
                supabase.table("workspaces").upsert({
                    "email": user_email, 
                    "workspace_data": novel_data_dict
                }).execute()
            except Exception:
                pass
                
        time.sleep(delay_time)
        
    st.session_state.worker_running = False

# ==========================================
# 4. GIAO DIỆN ĐĂNG NHẬP
# ==========================================
if not st.session_state.authenticated:
    st.title("⚡ Máy Dịch Truyện - Tối Giản")
    email = st.text_input("Email:")
    password = st.text_input("Mật khẩu:", type="password")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🚀 Đăng nhập", use_container_width=True):
            if supabase:
                try:
                    supabase.auth.sign_in_with_password({"email": email, "password": password})
                    st.session_state.authenticated = True; st.session_state.user_email = email
                    load_user_data_from_supabase(email)
                    st.rerun()
                except Exception as e: st.error(f"Lỗi: {e}")
            else:
                st.session_state.authenticated = True; st.session_state.user_email = email
                st.rerun()
    with col2:
        if st.button("📝 Đăng ký", use_container_width=True):
            if supabase:
                try: supabase.auth.sign_up({"email": email, "password": password}); st.success("Đăng ký thành công!")
                except Exception as e: st.error(f"Lỗi: {e}")
    st.stop()

# ==========================================
# 5. GIAO DIỆN CHÍNH
# ==========================================
st.sidebar.title("⚡ Menu")
menu = st.sidebar.radio("Chọn chức năng:", ["1. Cấu hình API", "2. Nguồn Truyện (Cào/Tải Raw)", "3. Dịch & Quản Lý"])
if st.sidebar.button("💾 Lưu Dữ Liệu"): save_user_data_to_supabase()
if st.sidebar.button("🚪 Đăng xuất"): st.session_state.authenticated = False; st.rerun()

# --- MENU 1: CẤU HÌNH API ---
if menu == "1. Cấu hình API":
    st.header("🔑 Cấu hình API & Model")
    st.session_state.novel_data["api_keys"]["gemini"] = st.text_area(
        "Gemini API Keys (Mỗi dòng 1 key):", 
        value=st.session_state.novel_data["api_keys"].get("gemini", ""), 
        height=150
    )
    
    model_options = [
        "Gemini 3.7 Flash (Mới nhất, Siêu tốc)",
        "Gemini 3.6 Flash (Cực nhanh, Tối ưu)",
        "Gemini 3.5 Flash (Thông minh, Ổn định)",
        "Gemini 3.1 Flash-Lite (Cực nhẹ, Raw dài)",
        "Gemini 2.5 Pro (Chất lượng cao, Văn phong mượt)",
        "Gemini 2.5 Flash (Bản tiêu chuẩn)"
    ]
    
    current_model = st.session_state.novel_data.get("selected_model", model_options[0])
    current_idx = model_options.index(current_model) if current_model in model_options else 0
    
    st.session_state.novel_data["selected_model"] = st.selectbox("Lựa chọn Model Dịch:", model_options, index=current_idx)
    st.session_state.novel_data["trans_prompt"] = st.text_area("Prompt Dịch:", value=st.session_state.novel_data.get("trans_prompt", ""), height=100)
    
    if st.button("💾 Lưu Cấu Hình", use_container_width=True): 
        save_user_data_to_supabase()
        st.success("✅ Đã lưu cấu hình!")

# --- MENU 2: NGUỒN TRUYỆN (CÀO TỪ WEB / TẢI FILE) ---
elif menu == "2. Nguồn Truyện (Cào/Tải Raw)":
    tab1, tab2 = st.tabs(["🌐 Cào Raw từ Web", "📂 Quản Lý File Raw & Tách Chương"])
    
    with tab1:
        st.subheader("Cào Raw từ nhiều Website (Bao gồm Zhihu)")
        urls_input = st.text_area("Nhập danh sách Link (mỗi link 1 dòng):", height=150)
        with st.expander("⚙️ (Tùy chọn) Nhập Cookie cho Zhihu trả phí"):
            custom_cookie = st.text_area("Dán Cookie Zhihu vào đây:")
            
        if st.button("🕷️ Bắt đầu cào & Lưu thành các File riêng lẻ", use_container_width=True):
            if urls_input.strip():
                urls = [u.strip() for u in urls_input.split('\n') if u.strip()]
                if "raw_docs" not in st.session_state.novel_data: st.session_state.novel_data["raw_docs"] = []
                
                success_count = 0
                with st.spinner(f"Đang cào {len(urls)} link..."):
                    for url in urls:
                        if "zhihu.com" in url:
                            raw_text, err_msg = scrape_zhihu_url(url, custom_cookie)
                            if err_msg:
                                st.error(f"❌ Lỗi link Zhihu {url}: {err_msg}")
                            elif raw_text:
                                safe_title = re.sub(r'[\\/*?:"<>|]', "", url.split('/')[-1]) or "zhihu_post"
                                file_name = f"Zhihu_{safe_title}_{datetime.now().strftime('%H%M%S')}.txt"
                                st.session_state.novel_data["raw_docs"].append({"filename": file_name, "content": raw_text})
                                success_count += 1
                        else:
                            title, scraped_text = scrape_web_chapter(url)
                            if "❌" not in scraped_text:
                                safe_title = re.sub(r'[\\/*?:"<>|]', "", title)[:30]
                                file_name = f"Web_{safe_title}_{datetime.now().strftime('%H%M%S')}.txt"
                                st.session_state.novel_data["raw_docs"].append({"filename": file_name, "content": scraped_text})
                                success_count += 1
                            else:
                                st.error(f"Lỗi ở link {url}: {scraped_text}")
                            
                if success_count > 0:
                    save_user_data_to_supabase()
                    st.success(f"🎉 Đã cào thành công {success_count} link!")
            else:
                st.warning("Vui lòng nhập ít nhất 1 link.")

        if st.session_state.novel_data.get("raw_docs"):
            st.write("---")
            st.subheader("📥 Quản lý File đã cào")
            for i, doc in enumerate(st.session_state.novel_data["raw_docs"]):
                with st.container(border=True):
                    col_name, col_down, col_del = st.columns([5, 2, 1])
                    with col_name:
                        new_name = st.text_input(f"Tên file {i+1}:", value=doc["filename"], key=f"rename_{i}")
                        if new_name != doc["filename"]:
                            st.session_state.novel_data["raw_docs"][i]["filename"] = new_name
                            save_user_data_to_supabase()
                    with col_down:
                        st.write(""); st.write("")
                        st.download_button("💾 Tải về", data=doc["content"], file_name=new_name if new_name.endswith('.txt') else f"{new_name}.txt", mime="text/plain", key=f"dl_btn_{i}", use_container_width=True)
                    with col_del:
                        st.write(""); st.write("")
                        if st.button("🗑️ Xóa", key=f"del_btn_{i}", use_container_width=True):
                            st.session_state.novel_data["raw_docs"].pop(i)
                            save_user_data_to_supabase()
                            st.rerun()

    with tab2:
        st.subheader("📂 Tải lên hoặc Quản Lý File Raw (.txt)")
        uploaded_file = st.file_uploader("Tải lên file tiểu thuyết tiếng Trung (.txt)", type=["txt"], key="txt_uploader")
        
        if uploaded_file is not None:
            existing_filenames = [d["filename"] for d in st.session_state.novel_data.get("raw_docs", [])]
            if uploaded_file.name not in existing_filenames:
                content = uploaded_file.read().decode('utf-8', errors='ignore')
                st.session_state.novel_data["raw_docs"].append({"filename": uploaded_file.name, "content": content})
                save_user_data_to_supabase()
                st.success(f"✅ Tải lên {uploaded_file.name} thành công!")
                time.sleep(1)
                st.rerun()
            
        if st.session_state.novel_data.get("raw_docs"):
            st.write("---")
            st.write("### 📚 Danh sách File Raw hiện có:")
            doc_names = [d["filename"] for d in st.session_state.novel_data["raw_docs"]]
            selected_doc_name = st.selectbox("Chọn File độc lập để Tách chương:", doc_names)
            selected_doc = next(d for d in st.session_state.novel_data["raw_docs"] if d["filename"] == selected_doc_name)
            
            st.info(f"File **{selected_doc_name}** đang có khoảng {len(selected_doc['content'])} ký tự.")
            
            regex_split = st.text_input("Regex Tách Chương:", value=DEFAULT_SPLIT_REGEX)
            
            if st.button("✂️ Bắt đầu Tách Chương từ File này", use_container_width=True):
                raw_text = selected_doc["content"]
                extracted_chapters = smart_split_novel(raw_text, regex_split)
                
                if not extracted_chapters: 
                    st.warning("Không tìm thấy chương nào theo quy tắc Regex trên!")
                else:
                    if "raw_chapters" not in st.session_state.novel_data: 
                        st.session_state.novel_data["raw_chapters"] = {}
                    
                    for t, c in extracted_chapters:
                        chap_name = f"[{selected_doc_name[:10]}] {t.strip()}"
                        st.session_state.novel_data["raw_chapters"][chap_name] = {"raw": f"{t.strip()}\n\n{c.strip()}", "translated": ""}
                        st.session_state.trans_status[chap_name] = "⏳ Đợi Dịch"
                        
                    save_user_data_to_supabase()
                    st.success(f"✅ Đã tách chính xác {len(extracted_chapters)} chương!")
                    time.sleep(1)
                    st.rerun()

            if st.button("🗑️ Xóa File này (Khỏi bộ nhớ)"):
                st.session_state.novel_data["raw_docs"] = [d for d in st.session_state.novel_data["raw_docs"] if d["filename"] != selected_doc_name]
                save_user_data_to_supabase()
                st.rerun()

# --- MENU 3: DỊCH & QUẢN LÝ ---
elif menu == "3. Dịch & Quản Lý":
    st.header("⏳ Hàng chờ & Dịch Thuật")
    
    with st.expander("📥 Tải File Raw Hoặc Dán Text Trực Tiếp Tại Đây", expanded=False):
        tab_up, tab_paste = st.tabs(["📁 Tải File .txt lên", "✍️ Dán Raw thủ công"])
        
        with tab_up:
            direct_file = st.file_uploader("Chọn file .txt cần đưa vào hàng chờ:", type=["txt"], key="direct_txt_uploader")
            direct_regex = st.text_input("Regex nhận diện tiêu đề chương:", value=DEFAULT_SPLIT_REGEX, key="direct_reg")
            auto_split_opt = st.checkbox("Tự động nhận diện và tách chương", value=True)
            
            if st.button("🚀 Nạp và Tách Chương Vào Hàng Chờ", key="btn_load_file_direct", use_container_width=True):
                if direct_file is not None:
                    raw_content = direct_file.read().decode('utf-8', errors='ignore')
                    if "raw_chapters" not in st.session_state.novel_data:
                        st.session_state.novel_data["raw_chapters"] = {}
                    
                    if auto_split_opt:
                        extracted = smart_split_novel(raw_content, direct_regex)
                        if extracted:
                            for t, c in extracted:
                                c_name = f"[{direct_file.name[:8]}] {t.strip()}"
                                st.session_state.novel_data["raw_chapters"][c_name] = {"raw": f"{t.strip()}\n\n{c.strip()}", "translated": ""}
                                st.session_state.trans_status[c_name] = "⏳ Đợi Dịch"
                            st.success(f"✅ Tách thành công {len(extracted)} chương chuẩn xác!")
                        else:
                            c_name = direct_file.name.replace(".txt", "")
                            st.session_state.novel_data["raw_chapters"][c_name] = {"raw": raw_content, "translated": ""}
                            st.session_state.trans_status[c_name] = "⏳ Đợi Dịch"
                            st.info(f"Không tìm thấy tiêu đề chương khớp, đã thêm nguyên file '{c_name}'.")
                    else:
                        c_name = direct_file.name.replace(".txt", "")
                        st.session_state.novel_data["raw_chapters"][c_name] = {"raw": raw_content, "translated": ""}
                        st.session_state.trans_status[c_name] = "⏳ Đợi Dịch"
                        st.success(f"✅ Đã thêm nguyên file '{c_name}' vào hàng chờ!")
                        
                    save_user_data_to_supabase()
                    time.sleep(1)
                    st.rerun()
                else:
                    st.warning("Vui lòng tải lên file .txt trước.")

        with tab_paste:
            quick_title = st.text_input("Tên / Tiêu đề:", value=f"Chương_{datetime.now().strftime('%H%M%S')}")
            quick_raw = st.text_area("Dán nội dung raw tiếng Trung vào đây:", height=150)
            if st.button("➕ Thêm vào hàng chờ", key="btn_paste_direct", use_container_width=True):
                if quick_raw.strip():
                    if "raw_chapters" not in st.session_state.novel_data:
                        st.session_state.novel_data["raw_chapters"] = {}
                    c_title = quick_title.strip()
                    st.session_state.novel_data["raw_chapters"][c_title] = {"raw": quick_raw.strip(), "translated": ""}
                    st.session_state.trans_status[c_title] = "⏳ Đợi Dịch"
                    save_user_data_to_supabase()
                    st.success(f"✅ Đã thêm '{c_title}' vào hàng chờ!")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.warning("Vui lòng nhập nội dung raw.")

    st.write("---")

    chapters = st.session_state.novel_data.get("raw_chapters", {})
    if not chapters:
        st.info("Chưa có chương nào trong hàng chờ dịch. Bạn có thể mở rộng khung **'📥 Tải File Raw Hoặc Dán Text Trực Tiếp Tại Đây'** ở trên để tải file và tách chương.")
    else:
        chap_keys = list(chapters.keys())
        st.write(f"**Tổng số chương hiện có:** {len(chap_keys)}")
        
        col1, col2 = st.columns(2)
        with col1: delay = st.number_input("Delay giữa các lần dịch (giây):", value=2.0, min_value=0.5, step=0.5)
        with col2: 
            # Nút Xóa sạch toàn bộ hàng chờ trên cả Cloud và Local
            if st.button("🗑️ Xóa toàn bộ hàng chờ (Reset)", type="primary"):
                reset_all_chapters()
                st.success("✅ Đã xóa sạch toàn bộ hàng chờ cũ!")
                time.sleep(0.5)
                st.rerun()
                
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            if st.button("🚀 Dịch Tự Động (3 Chương/Lần)", use_container_width=True):
                if not st.session_state.worker_running:
                    threading.Thread(
                        target=batch_worker, 
                        args=(
                            chap_keys, 
                            st.session_state.novel_data["api_keys"], 
                            st.session_state.novel_data["selected_model"], 
                            st.session_state.novel_data, 
                            st.session_state.trans_status, 
                            delay, 
                            st.session_state.user_email
                        ), 
                        daemon=True
                    ).start()
                    st.toast("✅ Đã bắt đầu dịch!", icon="🚀")
                else: st.toast("⚠️ Tiến trình dịch đang chạy rồi!", icon="⚠️")
        with col_btn2:
            if st.button("🔄 Làm mới giao diện", use_container_width=True):
                st.rerun()
        
        st.write("---")
        
        for k in chap_keys:
            if k not in st.session_state.trans_status: 
                status = "✅ Hoàn thành" if chapters[k].get("translated") else "⏳ Đợi Dịch"
                st.session_state.trans_status[k] = status
                
        st.subheader("📖 Xem & Chỉnh sửa bản dịch")
        options = [f"{k}   ---   ({st.session_state.trans_status[k]})" for k in chap_keys]
        selected_option = st.selectbox("Chọn chương muốn xem:", options)
        
        if selected_option:
            selected_key = selected_option.split("   ---   ")[0]
            
            col_act1, col_act2 = st.columns([4, 1])
            with col_act1:
                if st.button(f"✨ Dịch riêng chương này", key=f"btn_{selected_key}", use_container_width=True):
                    st.session_state.trans_status[selected_key] = "🔄 Đang dịch..."
                    process_single_chapter(
                        selected_key, 
                        chapters[selected_key]["raw"], 
                        st.session_state.novel_data["api_keys"], 
                        st.session_state.novel_data["selected_model"], 
                        st.session_state.novel_data, 
                        st.session_state.trans_status
                    )
                    save_user_data_to_supabase()
                    st.rerun()
            with col_act2:
                if st.button("🗑️ Xóa chương này", key=f"del_single_{selected_key}", use_container_width=True):
                    st.session_state.novel_data["raw_chapters"].pop(selected_key, None)
                    st.session_state.trans_status.pop(selected_key, None)
                    save_user_data_to_supabase()
                    st.rerun()
            
            col_raw, col_trans = st.columns(2)
            with col_raw:
                st.markdown("🇨🇳 **Bản Raw (Tiếng Trung)**")
                st.text_area("raw_text", chapters[selected_key]["raw"], height=500, label_visibility="collapsed")
            with col_trans:
                st.markdown("🇻🇳 **Bản Dịch (Tiếng Việt)**")
                st.text_area("trans_text", chapters[selected_key].get("translated", ""), height=500, label_visibility="collapsed")
                        
        st.write("---")
        if st.button("⬇️ Xuất EPUB", use_container_width=True):
            st.info("Chức năng xuất EPUB chuẩn bị ra mắt!")
