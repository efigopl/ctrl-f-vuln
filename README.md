# ctrl-f-vuln

## Purpose
ctrl-f-vuln is a tool for searching GitHub for code samples to identify vulnerabilities in open source projects. It provides both a command-line interface (CLI) for data collection and augmentation, and a Flask-based web application for interactive browsing, filtering, and marking files.

## Features
- Search GitHub code using custom queries and store results in a local SQLite database
- Augment repository data with stargazer counts
- Deduplicate files and repositories
- View project statistics
- Interactive web viewer with filtering, sorting, pagination, and marking files as checked

## Requirements
- Python 3.7+
- pip (Python package manager)
- GitHub API token (for code search and augmentation)

## Installation
1. Clone the repository:
   ```sh
   git clone https://github.com/efigopl/ctrl-f-vuln.git
   cd ctrl-f-vuln
   ```
2. Install dependencies:
   ```sh
   pip install -r requirements.txt
   ```
   If `requirements.txt` is missing, install manually:
   ```sh
   pip install flask requests python-decouple
   ```
3. Create a `.env` file with your GitHub token:
   ```sh
   echo "GITHUB_TOKEN=your_github_token_here" > .env
   ```

## Usage
### CLI Tool
Run the CLI tool for various operations:
```sh
python search_github.py <project> <command> [--query <search_query>] [--token <github_token>]
```
- `search`: Search GitHub code and store results
- `list`: List repositories for a project
- `resume`: Resume an interrupted search
- `augment`: Update stargazer counts for repositories
- `stats`: Show project statistics
- `config`: Generate a template .env file

#### Example:
```sh
python search_github.py php-search search --query "vulnerability" --token <your_token>
python search_github.py php-search augment
python search_github.py php-search stats
```

### Web Viewer
Start the Flask web app to browse and manage files:
```sh
python web_viewer.py
```
- Select project and filter by minimum stars
- Hide checked files using the checkbox
- Mark files as checked directly from the table
- Paginate through results (100 files per page)

Access the web app at: [http://127.0.0.1:5000/](http://127.0.0.1:5000/)

## Database Schema
- Projects
- Searches
- Repositories
- Files
- FileSearches

## Notes
- The GitHub API has rate limits. The tool uses batching and sleep intervals to avoid hitting limits.
- Sensitive information (like `.env` and database files) is excluded from version control via `.gitignore`.
