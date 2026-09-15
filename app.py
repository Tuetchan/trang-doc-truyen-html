import io
import os
import re
import time
from urllib.parse import urljoin
import zipfile
import requests
from bs4 import BeautifulSoup
import streamlit as st

# Thư viện bật hộp thoại chọn thư mục gốc của Windows/Mac/Linux
try:
    import tkinter as tk
    from tkinter import filedialog
    HAS_TKINTER = True
except ImportError:
    HAS_TKINTER = False

st.set_page_config(page_title="Cào Raw Tự Động Theo Thư Mục", page_icon="📁", layout="wide")

# ==========================================
# KHỞI TẠO BỘ NHỚ LƯU TRẠNG THÁI
# ==========================================
if 'folder_path' not in st.session_state:
    st.session_state['folder_path'] = os.path.abspath("./raw_truyen")

def select_folder():
    """Bật cửa sổ hệ thống để người dùng chọn thư mục"""
    if HAS_TKINTER:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        folder = filedialog.askdirectory(initialdir=st.session_state['folder_path'], title="Chọn thư mục lưu truyện")
        root.destroy()
        if folder:
            st.session_state['folder_path'] = os.path.abspath(folder)

# ==========================================
# CÁC HÀM XỬ LÝ VĂN BẢN
# ==========================================
def decode_text(response_content):
    for enc in ['utf-8', 'big5', 'gb18030', 'gbk']:
        try:
            return response_content.decode(enc)
        except UnicodeDecodeError:
            continue
    return response_content.decode('utf-8', errors='ignore')

def clean_file_name(filename):
    return re.sub(r'[\\/*?:"<>|]', "", filename).strip()

def html_to_clean_text(soup_obj):
    for br in soup_obj.find_all("br"):
        br.replace_with("\n")
    for tag in soup_obj.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
        tag.append('\n')
    lines = [line.strip() for line in soup_obj.get_text(strip=False).split('\n')]
    return "\n".join([l for l in lines if l])

def get_chapter_list(toc_url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'zh-TW,zh-CN,zh;q=0.9,en;q=0.7'
    }
    res = requests.get(toc_url.strip(), headers=headers, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(decode_text(res.content), 'html.parser')
    
    chapters = []
    target_container = soup.select_one('.chapter-list, ul.chapter-list, #chapter-list, .chapters, .catalog')
    container = target_container if target_container else soup
    
    for a in container.find_all('a', href=True):
        href = a['href']
        name = a.get_text(strip=True)
        if not name or len(name) < 2 or 'javascript' in href or href == '#':
            continue
        if re.search(r'(chapter|blog-entry|\.html|/n/|\d+)', href) or re.search(r'第.*?章|章|序|楔子', name):
            full_url = urljoin(toc_url, href)
            if not any(c['url'] == full_url for c in chapters):
                chapters.append({'title': name, 'url': full_url})
    return chapters

def scrape_chapter_body(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    res = requests.get(url, headers=headers, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(decode_text(res.content), 'html.parser')
    
    for tag in soup.find_all(['script', 'style', 'nav', 'aside', 'footer', 'header']):
        tag.decompose()
        
    content_node = soup.select_one('.content, .chapter-detail, #chapter-c, .entry-body, #chapter-content, .read-content')
    if content_node:
        return html_to_clean_text(content_node)
        
    best_text = ""
    for node in soup.find_all(['div', 'article']):
        txt = html_to_clean_text(node)
        if len(txt) > len(best_text):
            best_text = txt
    return best_text if len(best_text) > 80 else "Không lấy được nội dung chữ."

# ==========================================
# GIAO DIỆN CHÍNH
# ==========================================
st.title("📂 Công Cụ Cào & Tự Động Lưu Từng Chương Truyện")

url_toc = st.text_input("🔗 Nhập URL trang danh sách chương (Mục lục):", value="https://czbooks.net/n/sh300h")

# Khu vực chọn thư mục lưu trữ
st.markdown("### 1. Chọn Thư Mục Lưu Tệp Trên Máy Tính")
c1, c2 = st.columns([3, 1])
with c1:
    folder_input = st.text_input("Thư mục đích:", value=st.session_state['folder_path'], key="txt_folder")
    st.session_state['folder_path'] = folder_input
with c2:
    st.write(" ")
    st.write(" ")
    if st.button("📁 Duyệt Thư Mục...", on_click=select_folder, use_container_width=True):
        pass

c3, c4 = st.columns(2)
with c3:
    delay_time = st.number_input("⏱️ Tốc độ nghỉ giữa các chương (giây):", min_value=0.1, max_value=3.0, value=0.3, step=0.1)
with c4:
    limit_chapters = st.number_input("Giới hạn số chương cào (0 = cào hết):", min_value=0, value=0)

st.markdown("### 2. Tiến Hành Cào")
if st.button("🚀 Bắt Đầu Cào & Tự Động Lưu Từng Chương", type="primary", use_container_width=True):
    target_dir = os.path.abspath(st.session_state['folder_path'])
    os.makedirs(target_dir, exist_ok=True)
    
    with st.spinner("Đang phân tích danh sách các chương..."):
        try:
            chapters = get_chapter_list(url_toc)
        except Exception as e:
            chapters = []
            st.error(f"Lỗi khi đọc trang mục lục: {e}")
            
    if not chapters:
        st.error("Không tìm thấy danh sách chương nào, vui lòng kiểm tra lại link!")
    else:
        if limit_chapters > 0:
            chapters = chapters[:limit_chapters]
            
        total = len(chapters)
        st.info(f"Đã tìm thấy **{total}** chương. Đang tự động cào và ghi file trực tiếp vào: `{target_dir}`")
        
        prog_bar = st.progress(0)
        status_lbl = st.empty()
        
        # Buffer hỗ trợ tải file Zip dự phòng nếu cần
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zf:
            for idx, chap in enumerate(chapters, start=1):
                clean_name = clean_file_name(chap['title'])
                file_name = f"{idx:04d}_{clean_name}.txt"
                full_path = os.path.join(target_dir, file_name)
                
                status_lbl.text(f"⏳ Đang cào [{idx}/{total}]: {chap['title']}")
                
                try:
                    content = scrape_chapter_body(chap['url'])
                    full_content = f"{chap['title']}\n\n{content}"
                    
                    # 1. Ghi file trực tiếp vào thư mục ổ cứng đã chọn
                    with open(full_path, "w", encoding="utf-8-sig") as f:
                        f.write(full_content)
                        
                    # 2. Thêm vào zip backup
                    zf.writestr(file_name, full_content.encode('utf-8-sig'))
                except Exception as e:
                    with open(full_path, "w", encoding="utf-8-sig") as f:
                        f.write(f"Lỗi khi cào chương: {e}")
                        
                prog_bar.progress(idx / total)
                time.sleep(delay_time)
                
        st.success(f"🎉 ĐÃ HOÀN TẤT! Toàn bộ {total} chương đã được lưu thành công vào thư mục máy:")
        st.code(target_dir, language="bash")
        
        # Tạo nút download zip phòng trường hợp chạy từ xa
        st.download_button(
            label="📦 Tải Gói Nén .ZIP Toàn Bộ Chương (Dự phòng)",
            data=zip_buffer.getvalue(),
            file_name="Cac_Chuong_Raw.zip",
            mime="application/zip",
            use_container_width=True
        )
