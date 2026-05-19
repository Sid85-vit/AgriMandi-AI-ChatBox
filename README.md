# 🌱 Agri Mandi Chatbot

A local, AI-powered dashboard that tracks real-time Indian agricultural commodity prices using the official data.gov.in API and local LLMs.

## ⚠️ Prerequisites

Before running this project, you must have two things installed on your computer:
1. **Python 3.8+**
2. **[Ollama](https://ollama.com/)** (You must run `ollama run phi3` in your terminal at least once to download the local AI model).

## 🚀 Setup Instructions

**1. Clone the repository and install dependencies:**
Open your terminal inside the project folder and run:
`pip install -r requirements.txt`

**2. Setup your API Key:**
* Go to [data.gov.in](https://data.gov.in/) and create a free developer account to get an API Key.
* Create a file named `.env` in the root folder of this project.
* Add your key to the file like this: `GOV_API_KEY=your_key_here`

**3. Run the Backend Server:**
In your terminal, start the FastAPI server:
`python server.py`

**4. Launch the App:**
Simply double-click the `index.html` file to open it in your browser. The app will automatically sync with the government servers and your local Ollama instance.