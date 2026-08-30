import streamlit as st
import requests
import re
import time
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

# Regex xử lý sạch khoảng trắng tiếng Trung
DEFAULT_SPLIT_REGEX = r'(?:^|(?<=[。！？】”’"\'\s]))((?:【[^】\n]+】|(?:☆\s*、\s*)?(?:第\s*[0-9一二三四五六七八九十百千万零]+\s*[章回节集卷部]|(?:Chapter|Chương)\s*[0-9]+|[0-9]{1,5}\s*[、.．:：\s]))[^\u3000\r\n]{0,40}?)(?=\s*\u3000|\s{2,}|\n|$)'

if "authenticated" not in st.session_state: st.session_state.authenticated = False
if "user_email" not in st.session_state: st.session_state.user_email = ""
if "trans_status" not in st.session_state: st.session_state.trans_status = {}
if "novel_data" not in st.session_state:
    st.session_state.novel_data = {
        "api_keys": {"gemini": ""},
        "selected_model": "Gemini 3.6 Flash (Khuyên Dùng - Bản Mới Nhất)",
        "batch_size": 3, # Mặc định dịch 3 chương cùng lúc
        "raw_docs": [],
        "raw_chapters": {},
        "trans_prompt": "Bạn là một dịch giả tiểu thuyết chuyên nghiệp. Dịch mượt mà, thuần Việt, giữ nguyên đoạn văn và không tự ý thêm bớt tình tiết."
    }

if "is_translating" not in st.session_state:
    st.session_state.is_translating = False

# ==========================================
# 2. HÀM TÁCH CHƯƠNG THÔNG MINH
# ==========================================
def smart_split_novel(raw_text: str, custom_regex: str = "") -> list[tuple[str, str]]:
    reg = custom_regex.strip() if custom_regex and custom_regex.strip() else DEFAULT_SPLIT_REGEX
    try:
        pattern = re.compile(reg)
    except:
        pattern = re.compile(DEFAULT_SPLIT_REGEX)
    
    matches = list(pattern.finditer(raw_text))
    chapters = []
    
    if not matches:
        return []
    
    if matches[0].start() > 0:
        intro_content = raw_text[:matches[0].start()].strip()
        if len(re.sub(r'[\s\u3000\r\n]+', '', intro_content)) > 50:
            chapters.append(("Phần Mở Đầu / Tiền Truyện", intro_content))
            
    for i, match in enumerate(matches):
        if len(match.groups()) > 0 and match.group(1):
            raw_title = match.group(1).strip()
        else:
            raw_title = match.group(0).strip()
            
        start_idx = match.end()
        end_idx = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)
        content = raw_text[start_idx:end_idx].strip()
        
        safe_title = re.sub(r'[\r\n\u3000\t]+', ' ', raw_title)[:60].strip()
        
        if len(re.sub(r'[\s\u3000\r\n]+', '', content)) > 0:
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
        except Exception: 
            pass

def reset_all_chapters():
    st.session_state.novel_data["raw_chapters"] = {}
    st.session_state.trans_status = {}
    st.session_state.is_translating = False
    if supabase and st.session_state.user_email:
        try:
            supabase.table("workspaces").upsert({
                "email": st.session_state.user_email, 
                "workspace_data": st.session_state.novel_data
            }).execute()
        except Exception:
            pass

def export_combined_text(chapters_dict: dict) -> str:
    output = []
    for k, v in chapters_dict.items():
        translated = v.get("translated", "").strip()
        if translated and "❌" not in translated:
            output.append(f"=== {k} ===\n\n{translated}\n\n" + "="*40 + "\n\n")
    return "".join(output)

def export_zip_archive(chapters_dict: dict) -> bytes:
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for i, (k, v) in enumerate(chapters_dict.items()):
            translated = v.get("translated", "").strip()
            if translated and "❌" not in translated:
                safe_filename = re.sub(r'[\\/*?:"<>|]', "", k)[:40]
                zip_file.writestr(f"{i+1:03d}_{safe_filename}.txt", f"{k}\n\n{translated}")
    return zip_buffer.getvalue()

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

def _single_api_call(api_key, model_name, system_prompt, prompt_text):
    client = genai.Client(api_key=api_key)
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
    return res.text if (res and res.text) else None

def call_llm(system_prompt, prompt_text, api_keys, model_choice) -> tuple[bool, str]:
    gemini_keys = [k.strip() for k in re.split(r'[\n,;\s]+', api_keys.get("gemini", "")) if k.strip()]
    if not gemini_keys: 
        return False, "Chưa nhập Gemini API Key."
    
    if "3.7" in str(model_choice):
        models_to_try = ["gemini-3.7-flash", "gemini-3.6-flash"]
    else:
        models_to_try = ["gemini-3.6-flash"]

    last_error = ""
    
    for current_key in gemini_keys:
        key_exhausted = False
        
        for model_name in models_to_try:
            if key_exhausted:
                break 
                
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(_single_api_call, current_key, model_name, system_prompt, prompt_text)
                    # Nâng timeout lên 120 giây để tránh lỗi file dài
                    result_text = future.result(timeout=120) 
                    if result_text:
                        return True, result_text
                    else:
                        last_error = f"[{model_name}] Trả về rỗng."
            
            except concurrent.futures.TimeoutError:
                return False, f"Lỗi: Treo quá 120s (2 phút). Đoạn chữ quá dài nên AI chưa kịp dịch xong, hoặc mạng quá yếu."
                
            except Exception as e:
                err_str = str(e)
                last_error = f"[{model_name}] Lỗi: {err_str}"
                
                if "429" in err_str or "quota" in err_str.lower() or "exhausted" in err_str.lower():
                    key_exhausted = True
                    break 
                    
                elif "503" in err_str or "unavailable" in err_str.lower() or "high demand" in err_str.lower():
                    time.sleep(3.0)
                    continue 
                    
                elif "404" in err_str or "not found" in err_str.lower():
                    continue 
                else:
                    break 
                        
    return False, f"Thất bại: {last_error}"

def process_single_chapter(chap_key, raw_text, api_keys, model_choice, novel_data_dict, trans_status_dict, custom_prompt=None):
    base_prompt = custom_prompt if custom_prompt else novel_data_dict.get("trans_prompt", "Bạn là dịch giả.")
    system_prompt = base_prompt + "\n\n[LỆNH BẮT BUỘC]: Trả về trực tiếp bản dịch tiếng Việt mượt mà. Không giải thích thêm."
    
    success, result = call_llm(system_prompt, f"RAW CẦN DỊCH:\n\n{raw_text}", api_keys, model_choice)
    if success:
        novel_data_dict["raw_chapters"][chap_key]["translated"] = result
        trans_status_dict[chap_key] = "✅ Hoàn thành"
        return True
    else:
        novel_data_dict["raw_chapters"][chap_key]["translated"] = f"❌ {result}"
        trans_status_dict[chap_key] = f"❌ Lỗi (Xem bên dưới)"
        return False

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
menu = st.sidebar.radio("Chọn chức năng:", ["1. Cấu hình API", "2. Nguồn Truyện", "3. Dịch & Quản Lý"])
if st.sidebar.button("💾 Lưu Dữ Liệu"): 
    save_user_data_to_supabase()
    st.toast("Lưu thành công!", icon="✅")
if st.sidebar.button("🚪 Đăng xuất"): st.session_state.authenticated = False; st.rerun()

# --- MENU 1: CẤU HÌNH API ---
if menu == "1. Cấu hình API":
    st.header("🔑 Cấu hình API & Model")
    st.session_state.novel_data["api_keys"]["gemini"] = st.text_area(
        "Gemini API Keys (Mỗi dòng 1 key, tự động đổi key khi nghẽn):", 
        value=st.session_state.novel_data["api_keys"].get("gemini", ""), 
        height=150
    )
    
    model_options = [
        "Gemini 3.6 Flash (Khuyên Dùng - Bản Mới Nhất)",
        "Gemini 3.7 Flash (Thử nghiệm)"
    ]
    
    current_model = st.session_state.novel_data.get("selected_model", model_options[0])
    current_idx = model_options.index(current_model) if current_model in model_options else 0
    st.session_state.novel_data["selected_model"] = st.selectbox("Lựa chọn Model Dịch:", model_options, index=current_idx)
    
    st.session_state.novel_data["batch_size"] = st.slider(
        "Số chương dịch cùng lúc (Batch Size):", 
        min_value=1, max_value=5, 
        value=st.session_state.novel_data.get("batch_size", 3), 
        help="Giảm xuống 1 hoặc 2 nếu dùng API miễn phí để tránh lỗi Quá tải (Rate Limit)."
    )

    st.session_state.novel_data["trans_prompt"] = st.text_area("Prompt Dịch:", value=st.session_state.novel_data.get("trans_prompt", ""), height=100)
    
    if st.button("💾 Lưu Cấu Hình", use_container_width=True): 
        save_user_data_to_supabase()
        st.success("✅ Đã lưu cấu hình!")

# --- MENU 2: NGUỒN TRUYỆN ---
elif menu == "2. Nguồn Truyện":
    st.subheader("📂 Tải Lên Hoặc Cào Truyện Trực Tuyến")
    
    tab_upload, tab_crawl = st.tabs(["📁 Tải file Raw (.txt)", "🌐 Cào truyện từ Web (Zhihu/Khác)"])
    
    with tab_upload:
        uploaded_file = st.file_uploader("Tải lên file tiểu thuyết tiếng Trung (.txt)", type=["txt"], key="txt_uploader_v5")
        if uploaded_file is not None:
            existing_filenames = [d["filename"] for d in st.session_state.novel_data.get("raw_docs", [])]
            if uploaded_file.name not in existing_filenames:
                content = uploaded_file.read().decode('utf-8', errors='ignore')
                st.session_state.novel_data["raw_docs"].append({"filename": uploaded_file.name, "content": content})
                save_user_data_to_supabase()
                st.success(f"✅ Tải lên {uploaded_file.name} thành công!")
                time.sleep(1)
                st.rerun()

    with tab_crawl:
        st.markdown("Hỗ trợ tự động cào nội dung từ Zhihu (Kể cả bài VIP trả phí) và các trang web truyện thông thường.")
        url_input = st.text_input("🔗 Nhập Link truyện (URL):")
        cookie_input = st.text_area("🍪 Cookie Zhihu dạng JSON (Tùy chọn, dùng cho truyện VIP trả phí):", help="Sử dụng tiện ích EditThisCookie trên Chrome để xuất Cookie dạng JSON dán vào đây.")
        
        if st.button("⬇️ Cào Dữ Liệu", use_container_width=True, type="primary"):
            if not url_input.strip():
                st.warning("Vui lòng nhập Link truyện!")
            else:
                with st.spinner("Đang kết nối để tải nội dung..."):
                    if "zhihu.com" in url_input.lower():
                        content, err = scrape_zhihu_url(url_input, cookie_input)
                        title = f"Zhihu_{datetime.now().strftime('%H%M%S')}"
                    else:
                        title, content = scrape_web_chapter(url_input)
                        err = None if content and "Lỗi cào web" not in content else content
                    
                    if err or not content:
                        st.error(f"❌ Không thể tải truyện: {err or 'Nội dung rỗng'}")
                    else:
                        file_name = f"{title}.txt"
                        st.session_state.novel_data["raw_docs"].append({"filename": file_name, "content": content})
                        save_user_data_to_supabase()
                        st.success(f"✅ Đã cào truyện thành công! File '{file_name}' đã được thêm vào danh sách bên dưới.")
                        time.sleep(1.5)
                        st.rerun()

    if st.session_state.novel_data.get("raw_docs"):
        st.write("---")
        st.write("### 📚 Danh sách File Raw hiện có:")
        doc_names = [d["filename"] for d in st.session_state.novel_data["raw_docs"]]
        selected_doc_name = st.selectbox("Chọn File độc lập để Tách chương:", doc_names)
        selected_doc = next(d for d in st.session_state.novel_data["raw_docs"] if d["filename"] == selected_doc_name)
        
        st.info(f"File **{selected_doc_name}** đang có khoảng {len(selected_doc['content'])} ký tự.")
        
        regex_split = st.text_input("Regex Tách Chương (Đã được làm lại chống dính chữ):", value=DEFAULT_SPLIT_REGEX, key="regex_split_v5")
        
        if st.button("✂️ Bắt đầu Tách Chương từ File này", use_container_width=True):
            raw_text = selected_doc["content"]
            extracted_chapters = smart_split_novel(raw_text, regex_split)
            
            if not extracted_chapters: 
                st.warning("Không tìm thấy chương nào theo quy tắc Regex trên! Hãy kiểm tra lại nội dung truyện.")
            else:
                if "raw_chapters" not in st.session_state.novel_data: 
                    st.session_state.novel_data["raw_chapters"] = {}
                
                for t, c in extracted_chapters:
                    chap_name = f"[{selected_doc_name[:10]}] {t.strip()}"
                    st.session_state.novel_data["raw_chapters"][chap_name] = {"raw": f"{t.strip()}\n\n{c.strip()}", "translated": ""}
                    st.session_state.trans_status[chap_name] = "⏳ Đợi Dịch"
                    
                save_user_data_to_supabase()
                st.success(f"✅ Đã tách chính xác {len(extracted_chapters)} chương và đưa vào Hàng Chờ Dịch!")
                time.sleep(1)
                st.rerun()

        if st.button("🗑️ Xóa File này (Khỏi bộ nhớ)"):
            st.session_state.novel_data["raw_docs"] = [d for d in st.session_state.novel_data["raw_docs"] if d["filename"] != selected_doc_name]
            save_user_data_to_supabase()
            st.rerun()

# --- MENU 3: DỊCH & QUẢN LÝ ---
elif menu == "3. Dịch & Quản Lý":
    st.header("⏳ Hàng chờ & Dịch Thuật")
    
    with st.expander("📥 Tải File Raw Hoặc Dán Text Trực trực tiếp tại đây", expanded=False):
        tab_up, tab_paste = st.tabs(["📁 Tải File .txt lên", "✍️ Dán Raw thủ công"])
        
        with tab_up:
            direct_file = st.file_uploader("Chọn file .txt cần đưa vào hàng chờ:", type=["txt"], key="direct_txt_uploader_v5")
            direct_regex = st.text_input("Regex nhận diện tiêu đề chương:", value=DEFAULT_SPLIT_REGEX, key="direct_reg_v5")
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
                            st.info(f"Đã thêm nguyên file '{c_name}'.")
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
        st.info("Chưa có chương nào trong hàng chờ dịch.")
    else:
        chap_keys = list(chapters.keys())
        st.write(f"**Tổng số chương hiện có:** {len(chap_keys)}")
        
        col1, col2, col3 = st.columns([2, 2, 2])
        with col1: delay = st.number_input("Delay (giây):", value=1.0, min_value=0.5, step=0.5)
        with col2:
            if st.button("🗑️ Xóa toàn bộ hàng chờ (Reset)", type="primary", use_container_width=True):
                reset_all_chapters()
                st.success("✅ Đã xóa sạch toàn bộ hàng chờ!")
                time.sleep(0.5)
                st.rerun()
        with col3:
            if st.button("🧹 Khôi phục chương bị lỗi", use_container_width=True):
                for k in chap_keys:
                    if "Lỗi" in st.session_state.trans_status.get(k, "") or "Đang dịch" in st.session_state.trans_status.get(k, ""):
                        st.session_state.trans_status[k] = "⏳ Đợi Dịch"
                        if "❌" in chapters[k].get("translated", ""):
                            chapters[k]["translated"] = ""
                save_user_data_to_supabase()
                st.success("✅ Đã đưa các chương lỗi về trạng thái Đợi Dịch!")
                st.rerun()

        start_btn = st.button("🚀 BẮT ĐẦU DỊCH TỰ ĐỘNG (Cập nhật tiến trình trực tiếp)", type="primary", use_container_width=True)

        if start_btn:
            keys_to_translate = [
                k for k in chap_keys 
                if not chapters[k].get("translated") or "❌" in chapters[k].get("translated", "")
            ]
            
            if not keys_to_translate:
                st.success("🎉 Tất cả các chương đều đã được dịch xong!")
            else:
                progress_bar = st.progress(0.0)
                status_text = st.empty()
                
                # Lấy batch_size từ cấu hình (mặc định là 3 nếu chưa có)
                batch_size = st.session_state.novel_data.get("batch_size", 3)
                total_batches = (len(keys_to_translate) + batch_size - 1) // batch_size
                completed_count = 0
                
                for b_idx in range(0, len(keys_to_translate), batch_size):
                    batch = keys_to_translate[b_idx : b_idx + batch_size]
                    
                    status_text.markdown(f"**⚡ Đang dịch đợt {b_idx//batch_size + 1}/{total_batches}:** `{', '.join([k[:20] for k in batch])}`")
                    for k in batch:
                        st.session_state.trans_status[k] = "🔄 Đang dịch..."
                    
                    with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as executor:
                        future_to_key = {
                            executor.submit(
                                process_single_chapter,
                                k,
                                chapters[k]["raw"],
                                st.session_state.novel_data["api_keys"],
                                st.session_state.novel_data["selected_model"],
                                st.session_state.novel_data,
                                st.session_state.trans_status
                            ): k for k in batch
                        }
                        for future in concurrent.futures.as_completed(future_to_key):
                            completed_count += 1
                            progress_bar.progress(completed_count / len(keys_to_translate))
                            
                    save_user_data_to_supabase()
                    time.sleep(delay)
                    
                status_text.success("🎉 Đã hoàn tất đợt dịch tự động!")
                time.sleep(1)
                st.rerun()

        st.write("---")
        
        for k in chap_keys:
            if k not in st.session_state.trans_status: 
                status = "✅ Hoàn thành" if (chapters[k].get("translated") and "❌" not in chapters[k].get("translated")) else "⏳ Đợi Dịch"
                st.session_state.trans_status[k] = status
                
        st.subheader("📖 Xem & Chỉnh sửa bản dịch")
        options = [f"{k}   ---   ({st.session_state.trans_status[k]})" for k in chap_keys]
        selected_option = st.selectbox("Chọn chương muốn xem:", options)
        
        if selected_option:
            selected_key = selected_option.split("   ---   ")[0]
            
            col_act1, col_act2 = st.columns([4, 1])
            with col_act1:
                if st.button(f"✨ Dịch riêng chương này (Thử lại ngay)", key=f"btn_{selected_key}", use_container_width=True):
                    st.session_state.trans_status[selected_key] = "🔄 Đang dịch..."
                    with st.spinner("Đang gọi API dịch chương này..."):
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
        st.subheader("💾 Tải Toàn Bộ Bản Dịch Về Máy")
        
        done_chapters_count = sum(1 for v in chapters.values() if v.get("translated") and "❌" not in v.get("translated"))
        st.write(f"Số chương đã dịch hoàn chỉnh: **{done_chapters_count}/{len(chap_keys)}**")
        
        col_dl1, col_dl2, col_dl3 = st.columns(3)
        
        with col_dl1:
            combined_txt = export_combined_text(chapters)
            st.download_button(
                label="📄 Tải 1 File .TXT (Gộp tất cả)",
                data=combined_txt.encode('utf-8'),
                file_name=f"Ban_Dich_Gop_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                mime="text/plain",
                use_container_width=True,
                disabled=(done_chapters_count == 0)
            )
            
        with col_dl2:
            zip_data = export_zip_archive(chapters)
            st.download_button(
                label="📦 Tải File .ZIP (Tách từng file)",
                data=zip_data,
                file_name=f"Tung_Chuong_Zip_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
                mime="application/zip",
                use_container_width=True,
                disabled=(done_chapters_count == 0)
            )
            
        with col_dl3:
            json_str = json.dumps(st.session_state.novel_data, ensure_ascii=False, indent=2)
            st.download_button(
                label="🗄️ Tải Bản Sao Lưu .JSON",
                data=json_str.encode('utf-8'),
                file_name=f"Backup_Data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json",
                use_container_width=True
            )
