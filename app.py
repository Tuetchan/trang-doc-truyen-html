import streamlit as st
import requests
import re
import json
from datetime import datetime
from bs4 import BeautifulSoup

# ==========================================
# CẤU HÌNH TRANG
# ==========================================
st.set_page_config(page_title="Công Cụ Cào Raw Truyện", page_icon="🌐", layout="wide")

# ==========================================
# HÀM XỬ LÝ LỖI FONT CHỮ VÀ DỌN DẸP TEXT
# ==========================================
def decode_chinese_text(response_content):
    """Ép giải mã byte thô thành text, trị lỗi giun dế."""
    try:
        return response_content.decode('utf-8')
    except UnicodeDecodeError:
        try:
            return response_content.decode('gbk')
        except UnicodeDecodeError:
            return response_content.decode('utf-8', errors='ignore')

def html_to_clean_text(soup_obj):
    """
    Dọn dẹp HTML thông minh: Chỉ xuống dòng ở thẻ <p>, <div>, <br>
    Tránh tình trạng các dấu câu (dấu phẩy, dấu chấm) bị rớt dòng riêng lẻ.
    """
    # Đổi <br> thành ký tự xuống dòng
    for br in soup_obj.find_all("br"):
        br.replace_with("\n")
    
    # Thêm ký tự xuống dòng vào cuối các thẻ khối (block tags)
    for tag in soup_obj.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li']):
        tag.append('\n')
        
    # Lấy text thô (không dùng separator để giữ các thẻ span, b, i dính liền nhau)
    raw_text = soup_obj.get_text(strip=False)
    
    # Xóa khoảng trắng thừa và dòng trống
    lines = [line.strip() for line in raw_text.split('\n')]
    clean_text = "\n".join([line for line in lines if line])
    
    return clean_text

# ==========================================
# HÀM CÀO DỮ LIỆU
# ==========================================
def parse_zhihu_content(soup):
    texts = []
    script_tag = soup.find('script', id='js-initialData')
    if script_tag and script_tag.string:
        try:
            data = json.loads(script_tag.string)
            raw_contents = []
            
            def extract_contents(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k == 'content' and isinstance(v, str) and len(v) > 20:
                            raw_contents.append(v)
                        else:
                            extract_contents(v)
                elif isinstance(obj, list):
                    for item in obj:
                        extract_contents(item)
            
            extract_contents(data)
            
            for html_content in raw_contents:
                c_soup = BeautifulSoup(html_content, 'html.parser')
                texts.append(html_to_clean_text(c_soup))
        except Exception: 
            pass

    if not texts:
        content_nodes = soup.find_all(['div', 'section', 'article'], class_=re.compile(r'(Post-RichText|BodyModule|css-1y8291e|PaidColumn)', re.IGNORECASE))
        for node in content_nodes:
            txt = html_to_clean_text(node)
            if len(txt) > 100: texts.append(txt)

    if not texts:
        ps = soup.find_all('p')
        if len(ps) > 5: 
            texts = [p.get_text(strip=True) for p in ps if p.get_text(strip=True)]

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
        res.raise_for_status() 
        
        html_text = decode_chinese_text(res.content)
        soup = BeautifulSoup(html_text, 'html.parser')
        
        text = parse_zhihu_content(soup)
        return text if len(text) >= 50 else None, None
    except Exception as e: 
        return None, str(e)

def scrape_web_chapter(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        res = requests.get(url.strip(), headers=headers, timeout=15)
        res.raise_for_status()
        
        html_text = decode_chinese_text(res.content)
        soup = BeautifulSoup(html_text, 'html.parser')
        
        title_tag = soup.find('h1')
        title = title_tag.get_text().strip() if title_tag else ""
        if not title and soup.title: title = soup.title.string.strip()
        if not title: title = "Chương Web Mới"

        content_div = soup.select_one('#chapter-c, .chapter-content, #chapter-content, .box-chap, .story-detail-content, .read-content')
        if content_div:
            text = html_to_clean_text(content_div)
        else:
            ps = soup.find_all('p')
            if ps and len(ps) > 5: 
                text = "\n".join([p.get_text(strip=True) for p in ps if p.get_text(strip=True)])
            else: 
                text = html_to_clean_text(soup)
                
        return title, text if len(text) > 50 else "Không tìm thấy nội dung truyện ở link này."
    except Exception as e: 
        return "Lỗi", f"❌ Lỗi cào web: {str(e)}"

# ==========================================
# GIAO DIỆN CHÍNH
# ==========================================
st.title("🌐 Công Cụ Cào Raw Truyện (Chống Lỗi Font & Fix Ngắt Dòng)")
st.markdown("Hỗ trợ cào nội dung từ **Zhihu** và **các web truyện thông thường**, tự động giữ nguyên vẹn câu văn và đoạn văn.")

url_input = st.text_input("🔗 Nhập Link truyện (URL):")
cookie_input = st.text_area("🍪 Cookie Zhihu dạng JSON (Tùy chọn):", help="Nếu cào web thường thì bỏ trống.")

if st.button("⬇️ Cào Dữ Liệu", use_container_width=True, type="primary"):
    if not url_input.strip():
        st.warning("Vui lòng nhập Link truyện!")
    else:
        with st.spinner("Đang kết nối và dọn dẹp văn bản..."):
            if "zhihu.com" in url_input.lower():
                content, err = scrape_zhihu_url(url_input, cookie_input)
                title = f"Zhihu_{datetime.now().strftime('%H%M%S')}"
            else:
                title, content = scrape_web_chapter(url_input)
                err = None if content and "Lỗi cào web" not in content else content
            
            if err or not content:
                st.error(f"❌ Cào thất bại: {err or 'Nội dung rỗng'}")
            else:
                st.success("✅ Cào thành công! Văn bản đã được xếp lại ngay ngắn.")
                st.session_state['scraped_title'] = title
                st.session_state['scraped_content'] = content

# ==========================================
# PHẦN HIỂN THỊ VÀ NÚT TẢI XUỐNG
# ==========================================
if 'scraped_content' in st.session_state:
    st.write("---")
    title = st.session_state['scraped_title']
    content = st.session_state['scraped_content']
    
    st.subheader(f"📄 {title}")
    
    st.download_button(
        label="💾 Tải Raw Xuống File (.txt)",
        data=content.encode('utf-8-sig'),
        file_name=f"{title}.txt",
        mime="text/plain",
        use_container_width=True
    )
    
    st.text_area("Nội dung Raw (Xem trước):", content, height=400)
