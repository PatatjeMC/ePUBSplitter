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
from ebooklib import epub, ITEM_DOCUMENT, ITEM_IMAGE, ITEM_STYLE
from bs4 import BeautifulSoup
import os
import re
from urllib.parse import urlparse, unquote, urljoin
import zipfile
import tempfile
import posixpath
import collections

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

def normalize_canonical_href(href, base_href=None):
    if not href:
        return ''

    # 1. Unquote percent-encoding (e.g., %20 to space)
    cleaned_href = unquote(href)
    
    # 2. Remove URL fragment (e.g., #section1)
    cleaned_href = cleaned_href.split('#', 1)[0]

    # 3. If base_href is provided, join it with the href
    if base_href:
        # Ensure base_href itself is clean and acts like a directory for urljoin
        # For urljoin, base_href should end with / if it's a directory path
        # Example: base_href = "OEBPS/Text/chapter.xhtml"
        # We want to resolve relative to "OEBPS/Text/"
        # So, we can take posixpath.dirname(base_href) and add a slash
        base_dir = posixpath.dirname(base_href)
        if not base_dir.endswith('/'):
            base_dir += '/'
        # urljoin needs a full "URL-like" base, so we make it pseudo-absolute for joining
        # This ensures ../ are resolved correctly from the base_dir
        pseudo_absolute_base = f"file:///{base_dir}"
        resolved_url = urljoin(pseudo_absolute_base, cleaned_href)
        # Extract path part after file:///
        normalized_path = urlparse(resolved_url).path
        # Remove leading slash that urlparse.path might give for file URLs
        if normalized_path.startswith('/'):
            normalized_path = normalized_path[1:]
    else:
        normalized_path = cleaned_href
    
    # 4. Normalize path using posixpath to handle ".", "..", and multiple slashes,
    #    preserving forward slashes, which are standard in EPUBs.
    #    Example: "OEBPS/Text/../Images/img.jpg" -> "OEBPS/Images/img.jpg"
    normalized_path = posixpath.normpath(normalized_path)
    
    # 5. Remove leading slash if normpath added one (e.g. if original path was /OEBPS/...)
    #    or if it resulted from resolving an absolute-looking path.
    #    EPUB internal paths are typically relative to the archive root (e.g., "OEBPS/file.xhtml")
    if normalized_path.startswith('/'):
        normalized_path = normalized_path[1:]
        
    return normalized_path

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
            canonical_doc_href = normalize_canonical_href(href)
            results.append({'level': level, 'title': title, 'href': canonical_doc_href, 'last_href': None})
        if hasattr(item, 'subitems'):
            flatten_toc(item.subitems, level + 1, results)
        elif isinstance(item, tuple):
            _, children = item
            flatten_toc(children, level + 1, results)

    # Calculate last_href using a reverse iterator
    last_href = None
    for entry in reversed(results):
        if entry['href']:
            last_href = entry['href']
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
        # Attempt 1: xhtml_filename (from normalize_href) is the exact path
        try:
            # Ensure xhtml_filename is treated as a string, in case it's None or other type unexpectedly
            if not isinstance(xhtml_filename, str) or not xhtml_filename:
                raise KeyError("Invalid xhtml_filename provided")
            return epub_zip.read(xhtml_filename).decode('utf-8', errors='ignore')
        except KeyError:
            # Attempt 2: Find a file in the archive that ends with xhtml_filename.
            # This handles cases where xhtml_filename might be just a basename,
            # or if normalize_href didn't produce an exact match for some reason.
            # It also guards against xhtml_filename being None or empty.
            if isinstance(xhtml_filename, str) and xhtml_filename:
                for actual_path_in_zip in epub_zip.namelist():
                    if actual_path_in_zip.endswith(xhtml_filename):
                        # If found, actual_path_in_zip is the correct path to use
                        return epub_zip.read(actual_path_in_zip).decode('utf-8', errors='ignore')
        
        # If neither attempt found the file, or xhtml_filename was invalid
        raise FileNotFoundError(
            f"File '{xhtml_filename}' not found in ePUB archive. "
            f"Checked for exact match and suffix match. "
            f"Example files in archive: {epub_zip.namelist()[:5]}"
        )

def link_metadata(book, book_path, split_book, entry):
    language = book.get_metadata('DC', 'language')
    if language:
        split_book.set_language(str(language[0]) if isinstance(language, list) else str(language))
    authors = book.get_metadata('DC', 'creator')
    author_name = None
    for author_item in authors:
        if isinstance(author_item, tuple):
            author_name = author_item[0]
        else:
            author_name = str(author_item)
        if author_name:
            split_book.add_author(author_name)

    # Grab cover image
    # entry['href'] should be the canonical href of the first XHTML page of this section
    first_page_canonical_href = entry['href']
    if not first_page_canonical_href:
        print(f"⚠️ Entry '{entry['title']}' has no valid href, cannot extract cover.")
        return split_book

    try:
        # We need the raw content of the *specific first page* for its image tags
        cover_page_raw_content = extract_raw_xhtml(book_path, first_page_canonical_href)
    except FileNotFoundError:
        print(f"⚠️ Could not extract raw content for cover search from: {first_page_canonical_href}")
        return split_book
        
    soup = BeautifulSoup(cover_page_raw_content, 'html.parser')
    img_tag = soup.find('img')
    if img_tag and 'src' in img_tag.attrs:
        relative_cover_src = img_tag['src']
        
        # Resolve and normalize the cover image src relative to the first page's canonical href
        canonical_cover_href = normalize_canonical_href(relative_cover_src, base_href=first_page_canonical_href)
        
        cover_image_item = book.get_item_with_href(canonical_cover_href)
        if cover_image_item and cover_image_item.get_type() == ITEM_IMAGE:
            print(f"Found cover image: {canonical_cover_href} (from {relative_cover_src})")
            # Use the item's file_name (which should be canonical) and content
            # ebooklib's set_cover uses the item's file_name as the href in the OPF
            split_book.set_cover(cover_image_item.file_name, cover_image_item.get_content())
            # Ensure the cover image item itself is also added to the book's items if not already
            if not split_book.get_item_with_href(cover_image_item.file_name):
                 split_book.add_item(cover_image_item)
        elif cover_image_item:
            print(f"⚠️ Found item for cover '{canonical_cover_href}', but it's not an image (type: {cover_image_item.get_type()}).")
        else:
            print(f"⚠️ Could not find cover image item in book for resolved href: '{canonical_cover_href}' (from '{relative_cover_src}' in '{first_page_canonical_href}')")
    return split_book

def link_resources(book, split_book, raw_content, document_canonical_href, processed_resources_global):
    queue = collections.deque()
    
    # Find initial paths from the current raw_content (e.g., from an XHTML file)
    # These are relative to document_canonical_href
    initial_relative_paths = []
    src_matches = re.findall(r'src="([^"]+)"', raw_content)
    href_matches = re.findall(r'href="([^"]+)"', raw_content) # In XHTML, this primarily gets <link rel="stylesheet">
    css_url_matches = re.findall(r'url\s*\(\s*[\'"]?([^\'"\)]+)[\'"]?\s*\)', raw_content)
    initial_relative_paths.extend(src_matches)
    initial_relative_paths.extend(href_matches)
    initial_relative_paths.extend(css_url_matches)

    for relative_path_match in initial_relative_paths:
        if not relative_path_match or relative_path_match.startswith(('data:', 'http:', 'https:')):
            continue

        # Resolve path relative to the current document (XHTML or CSS)
        final_lookup_path = normalize_canonical_href(relative_path_match, base_href=document_canonical_href)
        
        if final_lookup_path and final_lookup_path not in processed_resources_global:
            # Check if item exists before adding to queue to avoid processing non-existent items later
            prospective_item = book.get_item_with_href(final_lookup_path)
            if prospective_item:
                queue.append(final_lookup_path)
                processed_resources_global.add(final_lookup_path) # Mark as processed/queued

    while queue:
        current_resource_href = queue.popleft()
        item = book.get_item_with_href(current_resource_href)

        if item:
            # Add the item to the split book if not already there
            if not split_book.get_item_with_href(item.file_name):
                split_book.add_item(item)

            # If the item is a CSS file, parse it for more resources
            is_css = (item.get_type() == ITEM_STYLE or 
                      (hasattr(item, 'media_type') and item.media_type == 'text/css'))

            if is_css:
                try:
                    css_content = item.get_content().decode('utf-8', errors='ignore')
                    css_internal_url_matches = re.findall(r'url\s*\(\s*[\'"]?([^\'"\)]+)[\'"]?\s*\)', css_content)
                    
                    for relative_path_in_css in css_internal_url_matches:
                        if not relative_path_in_css or relative_path_in_css.startswith(('data:', 'http:', 'https:')):
                            continue
                        
                        # Resolve path relative to the CSS file's location (current_resource_href)
                        linked_item_href = normalize_canonical_href(relative_path_in_css, base_href=current_resource_href)
                        
                        if linked_item_href and linked_item_href not in processed_resources_global:
                            prospective_linked_item = book.get_item_with_href(linked_item_href)
                            if prospective_linked_item:
                                queue.append(linked_item_href) 
                                processed_resources_global.add(linked_item_href)
                except Exception as e:
                    print(f"⚠️ Error processing CSS content for {current_resource_href}: {e}")
        else:
            print(f"⚠️ Resource not found in original EPUB when processing queue: '{current_resource_href}'")
            
    return split_book

def generate_toc(start_index, end_index, flat_toc):
    hierarchical_toc = []
    parent_lists_stack = [hierarchical_toc]

    if not flat_toc or start_index < 0 or start_index >= len(flat_toc) or \
       end_index < start_index or end_index >= len(flat_toc):
        if start_index >= 0 and start_index < len(flat_toc) and end_index >= len(flat_toc):
            end_index = len(flat_toc) - 1
        else:
            return []

    base_level_for_depth_calc = flat_toc[start_index]['level']
    toc_item_counter = 0
    level_shift_amount = 0

    # Determine if a level shift is needed.
    # This applies if the first item of the segment would be alone at its level,
    # and subsequent items are all deeper.
    if (start_index + 1) <= end_index: # Must have at least two items for a shift to be meaningful
        first_item_original_level = flat_toc[start_index]['level']
        second_item_original_level = flat_toc[start_index+1]['level']

        if second_item_original_level > first_item_original_level:
            # Check if the first item is the *only* one at its original level in this segment
            first_item_is_solitary_at_its_level = True
            for k_idx in range(start_index + 1, end_index + 1):
                if flat_toc[k_idx]['level'] == first_item_original_level:
                    first_item_is_solitary_at_its_level = False
                    break
            
            if first_item_is_solitary_at_its_level:
                # Calculate shift to make the second item a sibling of the first
                level_shift_amount = second_item_original_level - first_item_original_level

    for i_loop in range(start_index, end_index + 1):
        current_entry_data = flat_toc[i_loop]
        item_title = current_entry_data['title']
        item_href = current_entry_data['href']

        if not item_href:
            print(f"⚠️ Skipping TOC entry '{item_title}' (original index {i_loop}) due to missing href.")
            continue

        original_item_level = current_entry_data['level']
        item_level_for_hierarchy = original_item_level

        if level_shift_amount > 0 and i_loop > start_index: # Apply shift only to items after the first
            item_level_for_hierarchy = original_item_level - level_shift_amount
            # Ensure adjusted level doesn't go below the very first item's original level (our base)
            if item_level_for_hierarchy < base_level_for_depth_calc:
                item_level_for_hierarchy = base_level_for_depth_calc
        elif i_loop == start_index: # Ensure the first item itself uses its original level for hierarchy base
             item_level_for_hierarchy = original_item_level


        toc_item_counter += 1
        toc_item_id = f"splitnav-{toc_item_counter}"
        toc_item = epub.EpubHtml(title=item_title, file_name=item_href, uid=toc_item_id)

        effective_depth = item_level_for_hierarchy - base_level_for_depth_calc
        if effective_depth < 0: 
            effective_depth = 0

        while len(parent_lists_stack) > effective_depth + 1:
            parent_lists_stack.pop()
        
        current_parent_list = parent_lists_stack[-1]
        current_parent_list.append(toc_item)

        is_parent_to_next = False
        if (i_loop + 1) <= end_index:
            next_item_original_level = flat_toc[i_loop+1]['level']
            next_item_level_for_hierarchy = next_item_original_level
            if level_shift_amount > 0 and (i_loop + 1) > start_index: 
                next_item_level_for_hierarchy = next_item_original_level - level_shift_amount
                if next_item_level_for_hierarchy < base_level_for_depth_calc:
                     next_item_level_for_hierarchy = base_level_for_depth_calc
            
            # Compare with the current item's adjusted level for hierarchy
            if next_item_level_for_hierarchy > item_level_for_hierarchy:
                is_parent_to_next = True

        if is_parent_to_next:
            new_children_list = []
            current_parent_list[-1] = (toc_item, new_children_list) 
            parent_lists_stack.append(new_children_list)
            
    return hierarchical_toc

def split_epub(book, book_path, flat_toc, selected_entries, output_folder):
    # Create a new ePUB file for each selected entry.
    for entry in selected_entries:
        split_book = epub.EpubBook()
        title = entry['title']
        split_book.set_title(title)
        split_book.set_identifier(book.title + '-' + title.replace(' ', '_'))

        processed_resources_for_this_split_book = set()

        try:
            start_index = next(i for i, ft_entry in enumerate(flat_toc) if ft_entry['href'] == entry['href'] and ft_entry['title'] == entry['title'])
        except StopIteration:
            print(f"⚠️ Could not find entry '{title}' (href: {entry['href']}) in flat_toc. Skipping.")
            continue
        end_index = calculate_end_index(flat_toc, start_index)

        current_spine_items = []

        for i in range(start_index, end_index + 1):
            doc_canonical_href = flat_toc[i]['href']
            if not doc_canonical_href:
                print(f"⚠️ Skipping item with empty href at flat_toc index {i}, title: {flat_toc[i]['title']}")
                continue

            original_item = book.get_item_with_href(doc_canonical_href)
            if original_item:
                # Add the item to the split book. ebooklib uses original_item.file_name for the path.
                # original_item.file_name should be the same as doc_canonical_href if retrieved correctly.
                if not split_book.get_item_with_href(original_item.file_name):
                    split_book.add_item(original_item)
                
                if original_item.get_type() == ITEM_DOCUMENT:
                    current_spine_items.append(original_item) # Add to spine list for this book
                    try:
                        # Extract raw_content using the canonical href
                        raw_xhtml_content = extract_raw_xhtml(book_path, doc_canonical_href)
                        # Link resources using the canonical href of the current document
                        split_book = link_resources(book, split_book, raw_xhtml_content, doc_canonical_href, processed_resources_for_this_split_book)
                    except FileNotFoundError:
                        print(f"⚠️ Could not extract/link resources for: {doc_canonical_href}")
                # else: (item is not a document but was in flat_toc, e.g. an image as a TOC entry)
                #   It's already added if it's a resource. If it was meant for spine, that's unusual.
            else:
                print(f"⚠️ Could not find item for href: {doc_canonical_href} from flat_toc index {i}")
        
        # Link metadata using the entry (which contains the canonical href of its first page)
        if entry['href']: 
             split_book = link_metadata(book, book_path, split_book, entry)
        else:
            print(f"ℹ️ Skipping metadata linking for entry '{title}' due to missing main href.")

        split_book.toc = generate_toc(start_index, end_index, flat_toc) # generate_toc needs to use canonical hrefs
        split_book.add_item(epub.EpubNcx())
        split_book.add_item(epub.EpubNav()) # EpubNav is for EPUB3 nav document

        # Set the spine using the collected document items for this split book
        split_book.spine = current_spine_items
        if ADD_NAVIGATION and split_book.get_item_with_id('nav'): # Check if nav item exists
            # Ensure NAVIGATION_INDEX is valid for the current spine length
            nav_insert_pos = min(NAVIGATION_INDEX, len(split_book.spine))
            split_book.spine.insert(nav_insert_pos, split_book.get_item_with_id('nav'))
        elif ADD_NAVIGATION:
            print("⚠️ Requested to add navigation, but 'nav' item not found in split book.")

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
