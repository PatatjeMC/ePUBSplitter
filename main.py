# Copyright 2025 Noah
# https://patatje.dev
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import tkinter as tk
from tkinter import filedialog
from ebooklib import epub, ITEM_DOCUMENT, ITEM_IMAGE
from bs4 import BeautifulSoup
import os
import re
from urllib.parse import urlparse, unquote
import zipfile
import tempfile

_tk_root = None

def get_tk_root():
    global _tk_root
    if _tk_root is None:
        _tk_root = tk.Tk()
        _tk_root.withdraw()
    return _tk_root

def pick_epub_file():
    file_path = filedialog.askopenfilename(
        master=get_tk_root(),
        title="Select ePUB file",
        filetypes=[("ePUB files", "*.epub")]
    )
    return file_path

def pick_output_folder():
    folder_path = filedialog.askdirectory(
        master=get_tk_root(),
        title="Select Output Folder"
    )
    return folder_path

def calculate_end_index(flat_toc, current_index):
    current_level = flat_toc[current_index]['level']
    for i in range(current_index + 1, len(flat_toc)):
        if flat_toc[i]['level'] <= current_level:
            return i - 1
    return len(flat_toc) - 1


def normalize_href(href):
    if not href:
        return ''
    parsed = urlparse(href)
    unqouted = unquote(parsed.path)
    return unqouted.lstrip('../')

def flatten_toc(toc, level=1, results=None):
    if results is None:
        results = []

    def extract_href_and_title(item):
        if isinstance(item, epub.Link):
            return item.href, item.title
        if hasattr(item, 'title'):
            return getattr(item, 'href', None), item.title
        if isinstance(item, tuple):
            link_or_title, _ = item
            if isinstance(link_or_title, epub.Link):
                return link_or_title.href, link_or_title.title
            if hasattr(link_or_title, 'title'):
                return getattr(link_or_title, 'href', None), link_or_title.title
        return None, None

    for item in toc:
        href, title = extract_href_and_title(item)
        if title:
            results.append({'level': level, 'title': title, 'href': normalize_href(href), 'last_href': None})
        if hasattr(item, 'subitems'):
            flatten_toc(item.subitems, level + 1, results)
        elif isinstance(item, tuple):
            _, children = item
            flatten_toc(children, level + 1, results)

    # Calculate last_href using a reverse iterator
    last_href = None
    for entry in reversed(results):
        if entry['href']:
            last_href = normalize_href(entry['href'])
        entry['last_href'] = last_href

    return results

def print_toc_tree(flat_toc):
    print("\nParsed Table of Contents:\n")
    for i, entry in enumerate(flat_toc):
        indent = "  " * (entry['level'] - 1)
        print(f"{i+1}. {indent}{entry['title']} (Level {entry['level']}, href: {entry['href']})")

def parse_selection(input_str, max_index):
    selected = set()
    parts = input_str.split(',')
    for part in parts:
        if '-' in part:
            start, end = part.split('-')
            try:
                start, end = int(start), int(end)
                selected.update(range(start, end + 1))
            except ValueError:
                continue
        else:
            try:
                idx = int(part)
                if 1 <= idx <= max_index:
                    selected.add(idx)
            except ValueError:
                continue
    return sorted(selected)

def extract_raw_xhtml(epub_path, xhtml_filename):
    # Open the ePUB file as a ZIP archive
    with zipfile.ZipFile(epub_path, 'r') as epub_zip:
        # Detect the root folder dynamically
        root_folder = None
        for file_name in epub_zip.namelist():
            if file_name.endswith(xhtml_filename):
                root_folder = file_name.split('/')[0]
                break

        # If the root folder is not found, raise an error
        if not root_folder:
            raise FileNotFoundError(f"File '{xhtml_filename}' not found in ePUB archive.")

        # Construct the full path to the XHTML file
        full_path = f"{root_folder}/{xhtml_filename}"

        # Read the raw content of the XHTML file
        raw_content = epub_zip.read(full_path).decode('utf-8', errors='ignore')

        return raw_content

def link_metadata(book, book_path, split_book, raw_content, entry):
    language = book.get_metadata('DC', 'language')
    if language:
        split_book.set_language(str(language[0]) if isinstance(language, list) else str(language))
    authors = book.get_metadata('DC', 'creator')
    for author_item in authors:
        if isinstance(author_item, tuple):
            author_name = author_item[0]
        else:
            author_name = str(author_item)
        split_book.add_author(author_name)
    print("Author", author_name)

    # Grab cover image from the first page of the entry to use as cover
    cover_image = None
    page = book.get_item_with_href(entry['href'])
    soup = BeautifulSoup(extract_raw_xhtml(book_path, page.get_name() if page else entry['href']), 'html.parser')
    img_tag = soup.find('img')
    if img_tag and 'src' in img_tag.attrs:
        cover_href = normalize_href(img_tag['src'])
        cover_image = book.get_item_with_href(cover_href)
        print(f"Found cover image: {cover_href}")
        if cover_image and cover_image.get_type() == ITEM_IMAGE:
            split_book.set_cover(cover_href, cover_image.get_content())

    return split_book

def link_resources(book, split_book, raw_content):
    paths = set()
    # Find src attributes (images, audio, video)
    src_matches = re.findall(r'src="([^"]+)"', raw_content)
    # Find href attributes (stylesheets, fonts)
    href_matches = re.findall(r'href="([^"]+)"', raw_content)

    for match in src_matches + href_matches:
        match = normalize_href(match)
        if match:
            paths.add(match)

    for path in paths:
        item = book.get_item_with_href(path)
        if item:
            if item.get_type() is not ITEM_DOCUMENT:
                existing_item = split_book.get_item_with_href(path)
                if not existing_item:
                    split_book.add_item(item)

    return split_book

def generate_toc(book, start_index, end_index, flat_toc):
    old_ncx = book.get_item_with_id('ncx')
    soup = BeautifulSoup(old_ncx.get_content().decode('utf-8'), 'xml') if old_ncx else None
    toc = []
    if old_ncx:
        for i in range(start_index, end_index + 1):
            href = flat_toc[i]['href']
            nav_point = soup.find('navPoint', {'content': href})
            if nav_point and 'navLabel' in nav_point:
                nav_label = nav_point.find('navLabel')
                if nav_label:
                    nav_label_text = nav_label.find('text')
                    if nav_label_text:
                        toc.append(epub.EpubHtml(title=nav_label_text.text, file_name=href))
                        continue
            toc.append(epub.EpubHtml(title=flat_toc[i]['title'], file_name=href))

    # Ensure all items in the TOC have valid IDs
    for idx, item in enumerate(toc):
        if not item.get_id():
            item.id = f"navPoint-{idx + 1}"

    return toc

def split_epub(book, book_path, flat_toc, selected_entries, output_folder):
    # Create a new ePUB file for each selected entry.
    for entry in selected_entries:
        split_book = epub.EpubBook()
        title = entry['title']
        split_book.set_title(title)
        split_book.set_identifier(book.title + '-' + title.replace(' ', '_'))

        start_index = flat_toc.index(entry)
        end_index = calculate_end_index(flat_toc, start_index)
        for i in range(start_index, end_index + 1):
            item = book.get_item_with_href(flat_toc[i]['href'])
            if item and item.get_type() == ITEM_DOCUMENT:
                split_book.add_item(item)
                raw_content = extract_raw_xhtml(book_path, flat_toc[i]['href'])
                split_book = link_resources(book, split_book, raw_content)

        split_book = link_metadata(book, book_path, split_book, raw_content, entry)

        split_book.toc = generate_toc(book, start_index, end_index, flat_toc)
        split_book.add_item(epub.EpubNcx())
        split_book.add_item(epub.EpubNav())

        # Add the spine, optionally adding a navigation page
        split_book.spine = [item for item in split_book.get_items() if item.get_type() == ITEM_DOCUMENT]
        if ADD_NAVIGATION:
            split_book.spine.insert(NAVIGATION_INDEX, split_book.get_item_with_id('nav'))

        filename = re.sub(r'[\\/*?:"<>|]', "", title).strip() + ".epub"
        out_path = os.path.join(output_folder, filename)

        with tempfile.NamedTemporaryFile(delete=False, suffix='.epub') as temp_epub:
            temp_epub_path = temp_epub.name
            epub.write_epub(temp_epub_path, split_book)

        with zipfile.ZipFile(temp_epub_path, 'r') as temp_zip:
            with zipfile.ZipFile(out_path, 'w') as out_zip:
                all_entries = temp_zip.namelist()

                # Prepare a set of full paths that will be overwritten
                overwrite_paths = set()
                for i in range(start_index, end_index + 1):
                    href = flat_toc[i]['href']
                    match = next((n for n in all_entries if n.endswith(href)), None)
                    if match:
                        overwrite_paths.add(match)

                # Copy all files except the ones we want to overwrite
                for name in all_entries:
                    if name not in overwrite_paths:
                        data = temp_zip.read(name)
                        out_zip.writestr(name, data)

                # Overwrite the selected files with modified content
                for i in range(start_index, end_index + 1):
                    href = flat_toc[i]['href']
                    item = book.get_item_with_href(href)
                    if item and item.get_type() == ITEM_DOCUMENT:
                        raw_content = extract_raw_xhtml(book_path, href)
                        match = next((n for n in all_entries if n.endswith(href)), None)
                        if match:
                            print(f"Overwriting: {match}")
                            out_zip.writestr(match, raw_content.encode('utf-8'))
                        else:
                            print(f"⚠️ Could not find matching path for {href}, skipping.")

        os.remove(temp_epub_path)

        print(f"✅ Successfully created {filename} in {output_folder}")



if __name__ == "__main__":
    input_file = pick_epub_file()
    if not input_file:
        print("❌ No file selected. Exiting.")
        exit(1)
    if not os.path.isfile(input_file):
        print("File does not exist.")
        exit(1)

    output_folder = pick_output_folder()
    if not output_folder:
        print("❌ No output folder selected. Exiting.")
        exit(1)
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    book = epub.read_epub(input_file)
    flat_toc = flatten_toc(book.toc)
    print_toc_tree(flat_toc)

    while True:
        try:
            level = int(input("\nEnter the TOC level that contains the book split points (e.g. 1, 2, 3): "))
            entries = [entry for entry in flat_toc if entry['level'] == level]
            if not entries:
                print("⚠️ No entries found at that level. Try again.")
                continue
            break
        except ValueError:
            print("⚠️ Invalid input. Please enter a number.")

    print(f"\nFound {len(entries)} entries at level {level} that may represent book starts:")
    for i, entry in enumerate(entries):
        print(f"{i + 1}. {entry['title']}")

    print("\nEnter the numbers of entries you want to INCLUDE, separated by commas.")
    print("You can use ranges (e.g. 1,3,5-7). Leave empty to select all.")

    # Ask user to confirm settings
    user_input = input("Your selection: ").strip()
    if user_input == '' or user_input.lower() in ['all', 'a']:
        selected_indices = list(range(1, len(entries) + 1))
    else:
        selected_indices = parse_selection(user_input, len(entries))

    # Ask user if they want to add a new navigation menu
    add_nav = input("\nWould you like to add a new navigation menu to the split ePUBs? (y/n): ").strip().lower()
    if add_nav in ['yes', 'y']:
        global ADD_NAVIGATION
        ADD_NAVIGATION = True
        while True:
            try:
                nav_index = int(input("At which position in the spine should the navigation menu be inserted? (e.g. 2): "))
                global NAVIGATION_INDEX
                NAVIGATION_INDEX = nav_index
                break
            except ValueError:
                print("⚠️ Invalid input. Please enter a number.")
    else:
        ADD_NAVIGATION = False

    selected_entries = [entries[i - 1] for i in selected_indices]

    summary = []
    for i, entry in enumerate(selected_entries):
        summary.append(f"- {entry['title']} (Files {entry['href']} till {entry['last_href']})")

    print("\nYou selected:")
    for line in summary:
        print(line)

    confirm = input("\nDo you want to proceed with splitting the ePUB? (y/n): ").strip().lower()

    if confirm not in ['yes', 'y']:
        print("❌ Operation cancelled. Exiting.")
        exit(0)

    print(f"\nSplitting ePUB into {len(selected_entries)} parts...")

    split_epub(book, input_file, flat_toc, selected_entries, output_folder)
