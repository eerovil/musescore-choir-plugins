import dotenv
import os
from google import genai
from google.genai import types

import logging

logger = logging.getLogger(__name__)

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))


def get_gemini_api_key():
    # Load environment variables from .env file
    dotenv_path = os.path.join(CURRENT_DIR, "../../.env")
    dotenv.load_dotenv(dotenv_path)
    # Ensure GEMINI_API_KEY is set in the environment
    if "GEMINI_API_KEY" not in os.environ:
        raise ValueError("GEMINI_API_KEY is not set in the environment variables.")
    GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
    return GEMINI_API_KEY


def fix_lyrics(input_path, pdf_path):
    tsv_path: str = input_path.replace(".mscx", "_lyrics.tsv")
    prompt_path = os.path.abspath(os.path.join(CURRENT_DIR, "../lyric_prompt.txt"))
    output_path = input_path.replace(".mscx", "_lyrics_fixed.tsv")
    logger.info(f"Using PDF file: {pdf_path}")
    logger.info(f"Using prompt file: {prompt_path}")
    logger.info(f"Using TSV file: {tsv_path}")

    # if output_path already exists, return
    if os.path.exists(output_path):
        logger.info(f"Output file already exists: {output_path}")
        return

    try:
        client = genai.Client(api_key=get_gemini_api_key())
    except Exception as e:
        # Catch any errors related to the API client initialization
        logger.error(f"Failed to initialize Gemini API client: {e}")
        return

    # prompt is "prompt" and append the tsv file to it
    # And then also add the pdf file to the prompt
    prompt_lines = []
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt_lines = f.readlines()

    with open(tsv_path, "r", encoding="utf-8") as f:
        tsv_lines = f.readlines()

    tsv_line_count = len(
        [line for line in tsv_lines if line.strip()]
    )  # Count non-empty lines
    prompt_lines.insert(
        len(prompt_lines) - 1,
        f"Make sure result TSV line count is {tsv_line_count} (including header line)\n",
    )
    prompt_lines.append("\n\n")

    prompt_lines.extend(tsv_lines)
    prompt_lines.append("```\n")

    prompt = "".join(prompt_lines)

    try:
        # debug: pickle response and save it to a file
        import pickle

        response_path = input_path.replace(".mscx", "_response.pkl")

        # If the response file already exists, we can skip the API call
        if os.path.exists(response_path):
            logger.info(f"Response file already exists: {response_path}")
            with open(response_path, "rb") as f:
                response = pickle.load(f)
            logger.info("Loaded response from file.")
        else:
            logger.info("Generating content using Gemini API...")
            response = client.models.generate_content(
                model="gemini-2.5-flash-preview-05-20",
                contents=[
                    types.Part.from_text(text=prompt),
                    types.Part.from_bytes(
                        data=open(pdf_path, "rb").read(),
                        mime_type="application/pdf",
                    ),
                ],
            )

            with open(response_path, "wb") as f:
                pickle.dump(response, f)

        for candidate in response.candidates:
            if candidate.content.parts and candidate.content.parts[0].text:
                logger.info(f"Found valid candidate.")
                break

        fixed_lyrics = candidate.content.parts[0].text
        # all lines must start with either a number or "staff_id"
        fixed_lyrics = "\n".join(
            line
            for line in fixed_lyrics.splitlines()
            if line.startswith(
                ("staff_id", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0")
            )
        )
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(fixed_lyrics)

    except Exception as e:
        # Catch any other unexpected errors
        logger.error(f"An unexpected error occurred: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    import argparse

    parser = argparse.ArgumentParser(description="Fix lyrics in a MuseScore file.")
    parser.add_argument(
        "input_path", type=str, help="Path to the input MuseScore file (.mscx)."
    )
    parser.add_argument(
        "pdf_path", type=str, help="Path to the PDF file containing the lyrics."
    )

    args = parser.parse_args()

    fix_lyrics(args.input_path, args.pdf_path)
