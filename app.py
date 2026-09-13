import streamlit as st
import cloudscraper  # Thư viện chuyên vượt tường lửa (Cloudflare)
import re
import json
from datetime import datetime
from bs4 import BeautifulSoup

# ==========================================
# CẤU HÌNH TRANG
# ==========================================
st.set_page_config(page_title="Công Cụ Cào Raw Truyện (Bypass)", page_icon="🌐", layout="wide")

# ==========================================
# HÀM TẠO SCRAPER VƯỢT RÀO
# ==========================================
def get_bypass_scraper():
    # Giả lập hoàn toàn một trình duyệt Chrome trên Windows
    return cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        }
    )

# Bộ headers giả lập người dùng thật
REAL_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7,zh-CN;q=0.6,zh;q=0.5',
    'Sec-Ch-Ua': '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Upgrade-Insecure-Requests': '1'
}

# ==========================================
# HÀM CÀO DỮ LIỆU
# ==========================================
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
        scraper = get_bypass_scraper()
        headers = REAL_HEADERS.copy()
        
        cookie_val = custom_cookie.strip()
        if cookie_val:
            if cookie_val.startswith('[') and cookie_val.endswith(']'):
                try:
                    cookie_list = json.loads(cookie_val)
                    cookie_val = "; ".join([f"{c['name']}={c['value']}" for c in cookie_list if 'name' in c and 'value' in c])
                except Exception: pass
            headers['Cookie'] = cookie_val

        res = scraper.get(url, headers=headers, timeout=15)
        res.encoding = 'utf-8' # Ép cứng tránh lỗi font
        res.raise_for_status() 
        soup = BeautifulSoup(res.text, 'html.parser')
        text = parse_zhihu_content(soup)
        return text if len(text) >= 50 else None, None
    except Exception as e: 
        return None, str(e)

def scrape_web_chapter(url):
    try:
        scraper = get_bypass_scraper()
        res = scraper.get(url.strip(), headers=REAL_HEADERS, timeout=15)
        res.raise_for_status()
        
        # Sửa lỗi giun dế font chữ bằng byte decode
        try:
            html_text = res.content.decode('utf-8')
        except UnicodeDecodeError:
            try:
                html_text = res.content.decode('gbk')
            except UnicodeDecodeError:
                res.encoding = res.apparent_encoding or 'utf-8'
                html_text = res.text
                
        soup = BeautifulSoup(html_text, 'html.parser')
        
        title_tag = soup.find('h1')
        title = title_tag.get_text().strip() if title_tag else ""
        if not title and soup.title: title = soup.title.string.strip()
        if not title: title = "Chương Web Mới"

        content_div = soup.select_one('#chapter-c, .chapter-content, #chapter-content, .box-chap, .story-detail-content, .read-content')
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
        return "Lỗi", f"❌ Lỗi cào web hoặc bị chặn: {str(e)}"

# ==========================================
# GIAO DIỆN CHÍNH (Chỉ phần cào và tải xuống)
# ==========================================
st.title("🌐 Công Cụ Cào Raw Truyện (Bypass Anti-Bot)")
st.markdown("Hỗ trợ cào nội dung từ **Zhihu** và **các web truyện thông thường**, tự động lách bảo mật Cloudflare cơ bản.")

url_input = st.text_input("🔗 Nhập Link truyện (URL):")
cookie_input = st.text_area("🍪 Cookie Zhihu dạng JSON (Tùy chọn):", help="Nếu cào web thường thì bỏ trống.")

if st.button("⬇️ Cào Dữ Liệu", use_container_width=True, type="primary"):
    if not url_input.strip():
        st.warning("Vui lòng nhập Link truyện!")
    else:
        with st.spinner("Đang giả lập trình duyệt để vượt bảo mật..."):
            if "zhihu.com" in url_input.lower():
                content, err = scrape_zhihu_url(url_input, cookie_input)
                title = f"Zhihu_{datetime.now().strftime('%H%M%S')}"
            else:
                title, content = scrape_web_chapter(url_input)
                err = None if content and "Lỗi cào web" not in content else content
            
            if err or not content:
                st.error(f"❌ Trượt rào thất bại: {err or 'Nội dung rỗng'}")
            else:
                st.success("✅ Vượt rào thành công! Đã lấy được dữ liệu.")
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
