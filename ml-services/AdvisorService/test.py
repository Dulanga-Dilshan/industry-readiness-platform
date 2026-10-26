import os
from dotenv import load_dotenv
from google import genai
from google.genai import types


# Load API key from the local .env file
load_dotenv()

# Initialize the modern unified GenAI client
# It automatically picks up the GEMINI_API_KEY environment variable
client = genai.Client()

def get_embedding(text_chunk: str):
    """
    Step 1: Convert document text into vector math (Embeddings).
    Uses the modern active 'gemini-embedding-2' model.
    """
    response = client.models.embed_content(
        model="gemini-embedding-2", 
        contents=text_chunk,
    )
    
    # The new SDK returns a list of embedding objects.
    # Extract the numerical values from the first object in the list.
    return response.embeddings[0].values


def generate_rag_answer(user_question: str, retrieved_context: str):
    """
    Step 2: Ground the LLM by feeding it the question alongside 
    the documents retrieved from your database.
    """
    prompt = f"""
    You are a helpful assistant. Answer the question using ONLY the provided context.
    If the context does not contain the answer, say "I don't know".

    Context:
    {retrieved_context}

    Question: {user_question}
    Answer:
    """

    # Updated to use the modern, active gemini-3.8-flash model
    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
    )
    return response.text


# --- Quick Test Execution ---
if __name__ == "__main__":
    # Simulate a document chunk you retrieved from your vector DB
    mock_retrieved_document = (
        "Project Industry-Readiness Platform (IRP) was deployed on October 24, 2026. "
        "It uses a dual-branch Git architecture containing a 'main' branch and a developer branch named 'dulanga'."
    )
    
    query = "When was the Industry-Readiness Platform deployed and what branches exist?"

    print("--- 1. Testing Embedding Generation ---")
    vector = get_embedding(mock_retrieved_document)
    print(f"Generated Vector Length: {len(vector)} dimensions.")
    print(f"Sample values: {vector[:3]}...\n")

    try:
        print("--- 2. Testing Generative RAG Response ---")
        ai_answer = generate_rag_answer(user_question=query, retrieved_context=mock_retrieved_document)
        print(ai_answer)

    except Exception as e:
        print(ai_answer)
        print(type(ai_answer))
