# ePUBSplitter

ePUBSplitter is a tool that lets you split a single ePUB file into multiple separate ePUBs. This is especially useful when an ePUB contains several books, often from the same series, bundled together. Allowing you to extract each book as its own file.

> **Disclaimer:**  
> This project was quickly put together as a personal tool, with significant assistance from AI. It may be poorly structured, and I cannot guarantee that everything will work as expected.

## Usage

1. **Clone the repository:**

   ```bash
   git clone https://github.com/PatatjeMC/ePUBSplitter.git
   cd ePUBSplitter
   ```

2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Run the application:**

   ```bash
   python main.py
   ```

4. **Follow the prompts:**
   - The application will ask you to select the ePUB file you want to split.
   - You may be prompted to specify how to split the file.
   - After processing, the tool will save each split book as a separate ePUB file in the output directory.

## Requirements

- Python 3.x
- `ebooklib` library
- `beautifulsoup4` library
