import os
import re
import time
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
import streamlit as st

st.set_page_config(page_title="Cào Truyện Hàng Loạt Theo Mục Lục", page_icon="📚", layout="wide")

# ==========================================
# CÁC HÀM XỬ LÝ TEXT VÀ MÃ HÓA
# ==========================================
def decode_text(response_content):
    for enc in ['utf-8', 'big5', 'gb18030', 'gbk']:
        try:
            return response_content.decode(enc)
        except UnicodeDecodeError:
            continue
    return response_content.decode('utf-8', errors='ignore')

def clean_file_name(filename):
    """Xóa ký tự cấm đặt tên file trên Windows/Linux"""
    return re.sub(r'[\\/*?:"<>|]', "", filename).strip()

def html_to_clean_text(soup_obj):
    for br in soup_obj.find_all("br"):
        br.replace_with("\n")
    for tag in soup_obj.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
        tag.append('\n')
    lines = [line.strip() for line in soup_obj.get_text(strip=False).split('\n')]
    return "\n".join([l for l in lines if l])

# ==========================================
# 1. BÓC TÁCH MỤC LỤC CHƯƠNG (TỰ ĐỘNG)
# ==========================================
def get_chapter_list(toc_url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept-Language': 'zh-TW,zh-CN,zh;q=0.9,en;q=0.7'
    }
    res = requests.get(toc_url.strip(), headers=headers, timeout=15)
    res.raise_for_status()
    
    html = decode_text(res.content)
    soup = BeautifulSoup(html, 'html.parser')
    
    chapters = []
    # Bộ lọc link chương: Tìm trong các thẻ mục lục điển hình (czbooks: .chapter-list, ul.nav, #chapters,...)
    target_container = soup.select_one('.chapter-list, ul.chapter-list, #chapter-list, .chapters, .catalog')
    container = target_container if target_container else soup
    
    # Lấy các thẻ a có dấu hiệu link chương
    for a in container.find_all('a', href=True):
        href = a['href']
        name = a.get_text(strip=True)
        # Bỏ qua link menu hoặc link rác ngắn/trùng lặp
        if not name or len(name) < 2 or 'javascript' in href or href == '#':
            continue
        
        # Nhận diện đường dẫn chương (chứa chapter, entry, .html hoặc có chữ '第' trong tên)
        if re.search(r'(chapter|blog-entry|\.html|/n/|\d+)', href) or re.search(r'第.*?章|章|序|楔子', name):
            full_url = urljoin(toc_url, href)
            if not any(c['url'] == full_url for c in chapters):
                chapters.append({'title': name, 'url': full_url})
                
    return chapters

# ==========================================
# 2. CÀO NỘI DUNG TỪNG CHƯƠNG
# ==========================================
def scrape_chapter_body(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
    res = requests.get(url, headers=headers, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(decode_text(res.content), 'html.parser')
    
    # Gỡ bỏ các thẻ rác
    for tag in soup.find_all(['script', 'style', 'nav', 'aside', 'footer', 'header']):
        tag.decompose()
        
    # Thẻ chứa nội dung czbooks / web truyện thông dụng
    content_node = soup.select_one('.content, .chapter-detail, #chapter-c, .entry-body, #chapter-content, .read-content')
    
    if content_node:
        return html_to_clean_text(content_node)
        
    # Dự phòng quét thẻ dài nhất
    candidate_nodes = soup.find_all(['div', 'article'])
    best_text = ""
    for node in candidate_nodes:
        txt = html_to_clean_text(node)
        if len(txt) > len(best_text):
            best_text = txt
            
    return best_text if len(best_text) > 80 else "Không tìm thấy nội dung văn bản."

# ==========================================
# GIAO DIỆN STREAMLIT
# ==========================================
st.title("📚 Cào Từng Chương Truyện Tự Động Vào Thư Mục")

col1, col2 = st.columns([2, 1])
with col1:
    url_toc = st.text_input("🔗 Đường dẫn trang mục lục (Ví dụ czbooks):", value="https://czbooks.net/n/sh300h")
    save_folder = st.text_input("📁 Đường dẫn thư mục lưu trên máy tính:", value="./raw_truyen")
with col2:
    delay_time = st.number_input("⏱️ Giãn cách mỗi chương (giây):", min_value=0.1, max_value=5.0, value=0.5, step=0.1)
    limit_chapters = st.number_input("Giới hạn số chương cào (0 = toàn bộ):", min_value=0, value=0)

if st.button("🚀 Bắt Đầu Quét & Cào Dữ Liệu", type="primary"):
    if not url_toc:
        st.warning("Vui lòng nhập link mục lục!")
    else:
        # Tạo thư mục nếu chưa tồn tại
        os.makedirs(save_folder, exist_ok=True)
        
        with st.spinner("Đang phân tích danh sách chương..."):
            try:
                chapter_list = get_chapter_list(url_toc)
            except Exception as e:
                chapter_list = []
                st.error(f"Lỗi đọc trang mục lục: {e}")
        
        if not chapter_list:
            st.error("Không tìm thấy danh mục chương. Vui lòng kiểm tra lại link!")
        else:
            if limit_chapters > 0:
                chapter_list = chapter_list[:limit_chapters]
                
            total = len(chapter_list)
            st.info(f"Đã tìm thấy **{total}** chương. Bắt đầu tiến trình lưu file...")
            
            prog_bar = st.progress(0)
            status_text = st.empty()
            
            for index, chap in enumerate(chapter_list, start=1):
                clean_title = clean_file_name(chap['title'])
                # Đánh số thứ tự 4 chữ số (0001, 0002...) để tự sắp xếp đúng thứ tự trên máy tính
                file_name = f"{index:04d}_{clean_title}.txt"
                file_path = os.path.join(save_folder, file_name)
                
                status_text.text(f"[{index}/{total}] Đang cào: {chap['title']}")
                
                try:
                    content = scrape_chapter_body(chap['url'])
                    # Ghi ra file text định dạng UTF-8
                    with open(file_path, "w", encoding="utf-8-sig") as f:
                        f.write(f"{chap['title']}\n\n")
                        f.write(content)
                except Exception as e:
                    with open(file_path, "w", encoding="utf-8-sig") as f:
                        f.write(f"Lỗi khi cào chương: {e}")
                
                prog_bar.progress(index / total)
                time.sleep(delay_time)
                
            st.success(f"🎉 Hoàn tất! Tất cả các chương đã được lưu tại: `{os.path.abspath(save_folder)}`")
