import copy
import io
import re
import time
import uuid
import zipfile
from urllib.parse import urljoin, urlsplit, urldefrag

import requests
from bs4 import BeautifulSoup
import streamlit as st
import streamlit.components.v2 as components

st.set_page_config(page_title="Cào raw và lưu vào máy của bạn", layout="wide")

# Python chỉ giữ kết quả trong bộ nhớ. Trình duyệt ghi TXT vào thư mục người dùng chọn.
SAVE_JS = r"""
export default function({ parentElement, data }) {
    const button = parentElement.querySelector('[data-save]');
    const stop = parentElement.querySelector('[data-stop]');
    const status = parentElement.querySelector('[data-status]');
    const progress = parentElement.querySelector('progress');
    const errors = parentElement.querySelector('[data-errors]');
    const state = parentElement._rawSaveState || (parentElement._rawSaveState = {busy:false, stopped:false});
    const files = Array.isArray(data?.files) ? data.files : [];
    const supported = window.isSecureContext && typeof window.showDirectoryPicker === 'function';
    button.disabled = state.busy || !files.length || !supported;
    stop.disabled = !state.busy;
    if (!state.busy) {
        status.textContent = supported
            ? `Có ${files.length} tệp sẵn sàng. Bấm nút để chọn thư mục trên máy của bạn.`
            : 'Trình duyệt không hỗ trợ chọn thư mục ở trang này. Mở URL ứng dụng trực tiếp bằng Chrome/Edge trên máy tính qua HTTPS, hoặc tải ZIP bên dưới.';
    }
    stop.onclick = () => { state.stopped = true; status.textContent = 'Đang dừng sau tệp hiện tại...'; };
    button.onclick = async () => {
        if (state.busy) return;
        state.busy = true;
        state.stopped = false;
        button.disabled = true;
        errors.textContent = '';
        progress.value = 0;
        let saved = 0, failed = 0, folderName = '';
        const failures = [];
        try {
            // Phải gọi ngay từ thao tác bấm nút của người dùng, trước các await khác.
            const root = await window.showDirectoryPicker({mode:'readwrite', id:'raw-chapter-save'});
            folderName = 'Raw_' + new Date().toISOString().replace(/[:.]/g,'-') + '_' + crypto.randomUUID().slice(0,8);
            const directory = await root.getDirectoryHandle(folderName, {create:true});
            folderName = root.name + '/' + folderName;
            stop.disabled = false;
            for (let i = 0; i < files.length && !state.stopped; i++) {
                const file = files[i];
                status.textContent = `Đang lưu ${i+1}/${files.length}: ${file.name}`;
                try {
                    if (!file.name || /[\\/]/.test(file.name)) throw new Error('Tên tệp không hợp lệ.');
                    const handle = await directory.getFileHandle(file.name, {create:true});
                    const writer = await handle.createWritable();
                    try {
                        await writer.write(new Blob(['\ufeff', file.text], {type:'text/plain;charset=utf-8'}));
                        await writer.close();
                    } catch (err) {
                        try { await writer.abort(); } catch (_) {}
                        throw err;
                    }
                    saved++;
                } catch (err) {
                    failed++;
                    failures.push(file.name + ': ' + err.message);
                }
                progress.value = (i+1)/files.length;
            }
            status.textContent = `${state.stopped ? 'Đã dừng.' : 'Đã xử lý xong.'} Lưu được ${saved}/${files.length} tệp vào ${folderName}. Lỗi ghi: ${failed}.`;
            errors.textContent = failures.join('\n');
        } catch (err) {
            status.textContent = err.name === 'AbortError' ? 'Bạn đã hủy chọn thư mục. Kết quả cào vẫn còn.'
                : 'Không lưu được: ' + err.message + '. Bạn có thể tải ZIP bên dưới.';
        } finally {
            state.busy = false;
            button.disabled = !supported || !files.length;
            stop.disabled = true;
        }
    };
}
"""

folder_writer = components.component(
    "raw_local_folder_writer",
    html="""
    <div class="box">
      <div class="buttons">
        <button data-save>Chọn thư mục trên máy và lưu từng chương</button>
        <button data-stop disabled>Dừng lưu</button>
      </div>
      <p data-status role="status" aria-live="polite"></p>
      <progress max="1" value="0"></progress>
      <pre data-errors></pre>
    </div>
    """,
    css="""
    .box {font-family:var(--st-font);color:var(--st-text-color);padding:12px 0;}
    .buttons {display:flex;gap:10px;flex-wrap:wrap;}
    button {font:inherit;padding:10px 16px;border:1px solid #b8bec9;border-radius:8px;cursor:pointer;}
    button[data-save] {background:#166534;color:white;}
    button:disabled {opacity:.5;cursor:default;}
    progress {width:100%;height:18px;}
    pre {white-space:pre-wrap;max-height:180px;overflow:auto;font-size:12px;}
    """,
    js=SAVE_JS,
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "zh-TW,zh-CN,zh;q=0.9,en;q=0.7",
}


def decode_text(content, content_type=""):
    match = re.search(br'charset\s*=\s*[\"\']?([\w-]+)', content[:8192], re.I)
    declared = match.group(1).decode("ascii") if match else None
    if not declared:
        match = re.search(r'charset\s*=\s*[\"\']?([\w-]+)', content_type, re.I)
        declared = match.group(1) if match else None
    for encoding in [declared, "utf-8", "big5", "gb18030", "gbk"]:
        if encoding:
            try:
                return content.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                pass
    return content.decode("utf-8", errors="replace")


def clean_file_name(filename):
    value = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "", filename).strip().rstrip(". ")
    return value[:100] or "Chuong"


def html_to_clean_text(node):
    node = copy.deepcopy(node)
    for tag in node.find_all(["script", "style", "nav", "aside", "footer", "header", "noscript", "iframe"]):
        tag.decompose()
    for br in node.find_all("br"):
        br.replace_with("\n")
    for tag in node.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6"]):
        tag.insert(0, "\n")
        tag.append("\n")
    return "\n".join(line.strip() for line in node.get_text().splitlines() if line.strip())


def load_page(url, session):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("URL phải bắt đầu bằng http:// hoặc https://")
    res = session.get(url, headers=HEADERS, timeout=20)
    res.raise_for_status()
    soup = BeautifulSoup(decode_text(res.content, res.headers.get("Content-Type", "")), "html.parser")
    title = soup.title.get_text() if soup.title else ""
    if re.search(r"just a moment|access denied|verify you are human|attention required", title, re.I):
        raise ValueError("Trang nguồn đang chặn truy cập tự động.")
    return soup, res.url


def get_chapter_list(toc_url, session):
    soup, final_url = load_page(toc_url.strip(), session)
    container = soup.select_one(".chapter-list, #chapter-list, .chapters, .catalog") or soup
    chapters, seen = [], set()
    # Giữ điều kiện nhận diện link của mã gốc, thêm chống trùng URL.
    for a in container.find_all("a", href=True):
        href, name = a["href"].strip(), a.get_text(strip=True)
        if len(name) < 2 or not href or href.startswith("#") or "javascript:" in href.lower():
            continue
        if not (re.search(r"chapter|blog-entry|\.html|/n/|\d+", href, re.I) or re.search(r"第.*?[章页頁]|章|序|楔子", name)):
            continue
        full_url = urldefrag(urljoin(final_url, href))[0]
        if urlsplit(full_url).scheme not in ("http", "https") or full_url in seen:
            continue
        if urlsplit(full_url).netloc != urlsplit(final_url).netloc:
            continue
        seen.add(full_url)
        chapters.append({"title": name, "url": full_url})
    return chapters


def scrape_chapter_body(url, session):
    soup, _ = load_page(url, session)
    for tag in soup.find_all(["script", "style", "nav", "aside", "footer", "header"]):
        tag.decompose()
    for selector in ["#chapter-content", ".chapter-content", "#chapter-c", ".chapter-detail", ".entry-body", ".read-content", ".content"]:
        node = soup.select_one(selector)
        if node:
            text = html_to_clean_text(node)
            if text:
                return text
    best = max((html_to_clean_text(n) for n in soup.find_all(["div", "article"])), key=len, default="")
    if len(best) <= 80:
        raise ValueError("Không lấy được nội dung chữ.")
    return best


def make_zip(files, errors):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            archive.writestr(file["name"], file["text"].encode("utf-8-sig"))
        if errors:
            archive.writestr("_loi_cao.txt", "\n".join(errors).encode("utf-8-sig"))
    return buffer.getvalue()


def main():
    st.title("Cào raw và lưu vào máy của bạn")
    st.write("Cào xong, bấm chọn thư mục để lưu từng chương TXT trên máy đang mở trình duyệt.")
    st.caption("Dùng Chrome hoặc Edge trên máy tính. Mở trực tiếp URL ứng dụng qua HTTPS. Không ghi các chương thành tệp trên máy chủ.")
    with st.form("scrape_form"):
        url = st.text_input("URL mục lục", value="https://czbooks.net/n/sh300h")
        c1, c2 = st.columns(2)
        delay = c1.number_input("Nghỉ giữa các chương (giây)", min_value=0.1, max_value=10.0, value=0.3, step=0.1)
        limit = c2.number_input("Giới hạn chương (0 = tất cả)", min_value=0, value=0, step=1)
        start = st.form_submit_button("Bắt đầu cào", type="primary")
    if start:
        files, errors = [], []
        state = {"files": files, "errors": errors, "run_id": uuid.uuid4().hex, "total": 0}
        st.session_state.raw_result = state
        progress = st.progress(0)
        status = st.empty()
        try:
            with requests.Session() as session:
                status.info("Đang đọc mục lục...")
                chapters = get_chapter_list(url, session)
                if limit:
                    chapters = chapters[:int(limit)]
                state["total"] = len(chapters)
                if not chapters:
                    raise ValueError("Không tìm thấy link chương. Kiểm tra lại URL mục lục.")
                for i, chapter in enumerate(chapters, 1):
                    status.info(f"Đang cào {i}/{len(chapters)}: {chapter['title']}")
                    try:
                        body = scrape_chapter_body(chapter["url"], session)
                        files.append({"name": f"{i:04d}_{clean_file_name(chapter['title'])}.txt", "text": chapter["title"] + "\n\n" + body})
                    except Exception as exc:
                        errors.append(f"{i}. {chapter['title']} | {chapter['url']} | {exc}")
                    progress.progress(i / len(chapters))
                    if i < len(chapters):
                        time.sleep(delay)
                status.empty()
        except Exception as exc:
            status.error(str(exc))
        finally:
            state["zip"] = make_zip(files, errors) if files else None
    state = st.session_state.get("raw_result")
    if not state:
        return
    if state["files"]:
        st.success(f"Đã cào được {len(state['files'])}/{state['total']} tệp. Chưa lưu vào máy bạn. Bấm nút bên dưới để lưu.")
        folder_writer(data={"files": state["files"]}, key="folder_writer_" + state["run_id"])
        st.caption("Mỗi lần lưu tạo một thư mục con Raw_… để tránh ghi đè tệp cũ. Giữ tab mở trong lúc lưu.")
        st.download_button("Tải ZIP nếu không chọn được thư mục", data=state["zip"], file_name="Cac_Chuong_Raw.zip", mime="application/zip", on_click="ignore")
    elif state["total"]:
        st.error("Chưa cào được chương nào.")
    if state["errors"]:
        with st.expander(f"Xem {len(state['errors'])} lỗi cào"):
            st.text("\n".join(state["errors"]))


if __name__ == "__main__":
    main()
