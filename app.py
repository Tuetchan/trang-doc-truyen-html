
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
