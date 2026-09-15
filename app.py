import streamlit as st
import requests
import re
import json
from datetime import datetime
from bs4 import BeautifulSoup

# ==========================================
# CẤU HÌNH TRANG
# ==========================================
st.set_page_config(page_title="Công Cụ Cào Raw Truyện Đa Năng", page_icon="🌐", layout="wide")

# ==========================================
# HÀM XỬ LÝ LỖI FONT CHỮ VÀ DỌN DẸP TEXT
# ==========================================
def decode_chinese_text(response_content):
    """Ép giải mã byte thô thành text, hỗ trợ UTF-8, GBK và Big5 (cho blog Đài/Hồng Kông)."""
    for enc in ['utf-8', 'gb18030', 'big5', 'gbk']:
        try:
            return response_content.decode(enc)
        except UnicodeDecodeError:
            continue
    return response_content.decode('utf-8', errors='ignore')

def clean_unwanted_elements(soup_obj):
    """Loại bỏ thẻ rác, sidebar, menu điều hướng để tránh cào nhầm cột phụ."""
    for element in soup_obj.find_all(['script', 'style', 'nav', 'aside', 'footer', 'header', 'noscript', 'iframe']):
        element.decompose()
        
    # Xóa các class phổ biến của thanh bên (sidebar/menu/comment)
    sidebar_pattern = re.compile(r'(sidebar|widget|comment|menu|nav|header|footer|paging)', re.IGNORECASE)
    for tag in soup_obj.find_all(attrs={'class': sidebar_pattern}):
        tag.decompose()
    for tag in soup_obj.find_all(attrs={'id': sidebar_pattern}):
        tag.decompose()

def html_to_clean_text(soup_obj):
    """Dọn dẹp HTML thông minh: Giữ xuống dòng hợp lý, dính các thẻ inline."""
    # Xử lý thẻ ngắt dòng
    for br in soup_obj.find_all("br"):
        br.replace_with("\n")
    
    # Xuống dòng sau các khối văn bản
    for tag in soup_obj.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote']):
        tag.append('\n')
        
    raw_text = soup_obj.get_text(strip=False)
    lines = [line.strip() for line in raw_text.split('\n')]
    return "\n".join([line for line in lines if line])

# ==========================================
# HÀM TRÍCH XUẤT NỘI DUNG TỰ ĐỘNG THEO BỐ CỤC
# ==========================================
def extract_smart_content(soup):
    """Tự động phát hiện cột nội dung chính (bên trái hoặc phải)."""
    # 1. Các selector phổ biến của FC2 Blog, WordPress, Blogger, Web Raw
    selectors = [
        '.entry-body', '.entry_body', '.entry-content', '.entry_content', # FC2 / WP
        '.post-body', '.post_body', 'article', 'main',                    # Blogger / Modern CMS
        '#chapter-c', '.chapter-content', '#chapter-content',             # Web truyện Tàu
        '.box-chap', '.story-detail-content', '.read-content',
        '#article_content', '.content-body', '.txtnav'
    ]
    
    for selector in selectors:
        target = soup.select_one(selector)
        if target:
            text = html_to_clean_text(target)
            if len(text) > 150:
                return text

    # 2. Thuật toán dự phòng: Quét toàn bộ các thẻ div/section để tìm thẻ có lượng text dài nhất
    candidate_nodes = soup.find_all(['div', 'section', 'article'])
    best_text = ""
    max_len = 0
    
    for node in candidate_nodes:
        # Bỏ qua nếu là thẻ con nằm quá sâu hoặc chứa ít hơn 2 đoạn p
        ps = node.find_all('p')
        if len(ps) < 2 and len(node.get_text(strip=True)) < 200:
            continue
            
        current_text = html_to_clean_text(node)
        if len(current_text) > max_len:
            max_len = len(current_text)
            best_text = current_text
            
    if best_text and max_len > 100:
        return best_text

    # 3. Kế sách cuối cùng: gom tất cả thẻ p còn lại
    paragraphs = [p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 20]
    return "\n\n".join(paragraphs) if paragraphs else ""

# ==========================================
# CÀO ZHIHU VÀ WEB TỔNG HỢP
# ==========================================
def scrape_zhihu_content(soup):
    texts = []
    script_tag = soup.find('script', id='js-initialData')
    if script_tag and script_tag.string:
        try:
            data = json.loads(script_tag.string)
            raw_contents = []
            def extract(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k == 'content' and isinstance(v, str) and len(v) > 20:
                            raw_contents.append(v)
                        else: extract(v)
                elif isinstance(obj, list):
                    for item in obj: extract(item)
            extract(data)
            for html in raw_contents:
                texts.append(html_to_clean_text(BeautifulSoup(html, 'html.parser')))
        except Exception:
            pass
    return "\n\n".join(texts) if texts else ""

def scrape_any_page(url, custom_cookie=""):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh-CN,zh;q=0.9,en;q=0.7',
        }
        
        cookie_val = custom_cookie.strip()
        if cookie_val:
            if cookie_val.startswith('[') and cookie_val.endswith(']'):
                try:
                    c_list = json.loads(cookie_val)
                    cookie_val = "; ".join([f"{c['name']}={c['value']}" for c in c_list if 'name' in c and 'value' in c])
                except Exception:
                    pass
            headers['Cookie'] = cookie_val

        res = requests.get(url.strip(), headers=headers, timeout=15)
        res.raise_for_status()

        html_text = decode_chinese_text(res.content)
        soup = BeautifulSoup(html_text, 'html.parser')

        # Lấy tiêu đề
        title_tag = soup.find(['h1', 'h2'])
        title = title_tag.get_text().strip() if title_tag else ""
        if not title and soup.title:
            title = soup.title.string.strip()
        title = re.sub(r'[\\/*?:"<>|]', "", title)[:60] or "Raw_Content"

        # Phân loại website
        if "zhihu.com" in url.lower():
            content = scrape_zhihu_content(soup)
            if not content:
                clean_unwanted_elements(soup)
                content = extract_smart_content(soup)
        else:
            clean_unwanted_elements(soup)
            content = extract_smart_content(soup)

        if not content or len(content.strip()) < 50:
            return title, None, "Không bóc tách được nội dung chính (Trang có thể chặn bot hoặc nội dung quá ngắn)."
            
        return title, content, None

    except Exception as e:
        return "Lỗi", None, str(e)

# ==========================================
# GIAO DIỆN
# ==========================================
st.title("🌐 Công Cụ Cào Raw Đa Năng (Auto Tách Cột Blog/FC2/Zhihu)")

url_input = st.text_input("🔗 Nhập Link truyện (Hỗ trợ FC2, Blogspot, Zhihu, 69shu,...):")
cookie_input = st.text_area("🍪 Cookie (Tùy chọn - Dành cho tài khoản VIP/Zhihu):", height=70)

if st.button("⬇️ Cào Dữ Liệu", use_container_width=True, type="primary"):
    if not url_input.strip():
        st.warning("Vui lòng dán đường dẫn (URL) cần cào!")
    else:
        with st.spinner("Đang loại bỏ sidebar/menu và trích xuất nội dung..."):
            title, content, err = scrape_any_page(url_input, cookie_input)
            
            if err:
                st.error(f"❌ Cào thất bại: {err}")
            else:
                st.success("✅ Đã nhận biết và cào đúng cột nội dung!")
                st.session_state['scraped_title'] = title
                st.session_state['scraped_content'] = content

if 'scraped_content' in st.session_state:
    st.write("---")
    title = st.session_state['scraped_title']
    content = st.session_state['scraped_content']
    
    st.subheader(f"📄 {title}")
    st.download_button(
        label="💾 Tải Raw Xuống (.txt)",
        data=content.encode('utf-8-sig'),
        file_name=f"{title}.txt",
        mime="text/plain",
        use_container_width=True
    )
    st.text_area("Nội dung trích xuất:", content, height=450)
