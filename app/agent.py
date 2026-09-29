# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0 (the "License");

import json
import os
import subprocess
import urllib.parse
import urllib.request
import uuid
from typing import List, Optional

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.cloud import firestore, storage
from google.oauth2.credentials import Credentials
import google.auth

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.genai import types

from .a2ui_utils import a2ui_callback

# HARDCODED GCP Project ID string & GCS bucket
FIRESTORE_PROJECT_ID = "qwiklabs-gcp-01-d9763d3935dc"
GCS_BUCKET_NAME = "smart-recipe-assets-qwiklabs-gcp-01-d9763d3935dc"
MEMORY_BANK_ID = "7269317772948013056"


def get_agent_engine_resource_name() -> str:
    """Reads the remote_agent_runtime_id from deployment_metadata.json."""
    metadata_path = os.path.join(
        os.path.dirname(__file__), "..", "deployment_metadata.json"
    )
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, "r") as f:
                data = json.load(f)
                runtime_id = data.get("remote_agent_runtime_id")
                if runtime_id:
                    return runtime_id
        except Exception:
            pass
    return f"projects/757772688676/locations/us-east1/reasoningEngines/{MEMORY_BANK_ID}"


def get_firestore_client() -> firestore.Client:
    """Gets a Firestore client using the hardcoded project ID, supporting local gcloud auth and deployed service accounts."""
    try:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"], text=True
        ).strip()
        if token:
            return firestore.Client(
                project=FIRESTORE_PROJECT_ID, credentials=Credentials(token)
            )
    except Exception:
        pass
    return firestore.Client(project=FIRESTORE_PROJECT_ID)


def search_recipes(cuisine: str = "", dietary_tag: str = "") -> str:
    """Searches the Firestore recipe catalog by cuisine or dietary tag (e.g. vegan, gluten-free).

    Args:
        cuisine: Optional cuisine filter (e.g. Asian, Mediterranean).
        dietary_tag: Optional dietary tag filter (e.g. vegan, gluten-free).

    Returns:
        A JSON string containing matching recipe details.
    """
    db = get_firestore_client()
    docs = db.collection("recipes").stream()
    recipes = []
    for doc in docs:
        data = doc.to_dict()
        match = True
        if cuisine and cuisine.lower() not in data.get("cuisine", "").lower():
            match = False
        if dietary_tag:
            tags = [t.lower() for t in data.get("dietary_tags", [])]
            if dietary_tag.lower() not in tags:
                match = False
        if match:
            recipes.append(data)
    return json.dumps(recipes, indent=2)


def add_recipe(
    title: str,
    cuisine: str,
    prep_time_mins: int,
    dietary_tags: List[str],
    ingredients: List[str],
    instructions: str,
    calories_per_serving: int,
) -> str:
    """Adds a new recipe to the Firestore recipe catalog.

    Args:
        title: Title of the recipe.
        cuisine: Cuisine type.
        prep_time_mins: Preparation time in minutes.
        dietary_tags: List of dietary tags (e.g. ['vegan', 'gluten-free']).
        ingredients: List of ingredient strings.
        instructions: Cooking instructions.
        calories_per_serving: Estimated calories per serving.

    Returns:
        Confirmation message with the recipe ID.
    """
    db = get_firestore_client()
    doc_id = f"recipe_{title.lower().replace(' ', '_')}"
    recipe_data = {
        "id": doc_id,
        "title": title,
        "cuisine": cuisine,
        "prep_time_mins": prep_time_mins,
        "dietary_tags": dietary_tags,
        "ingredients": ingredients,
        "instructions": instructions,
        "calories_per_serving": calories_per_serving,
    }
    db.collection("recipes").document(doc_id).set(recipe_data)
    return f"Successfully saved recipe '{title}' to Firestore with ID: {doc_id}."


def get_pantry_items() -> str:
    """Retrieves all current ingredients and quantities stored in the Firestore pantry inventory.

    Returns:
        A JSON string listing all pantry items.
    """
    db = get_firestore_client()
    docs = db.collection("pantry_items").stream()
    items = [doc.to_dict() for doc in docs]
    return json.dumps(items, indent=2)


def update_pantry_item(
    item_name: str, quantity: str, category: str = "general"
) -> str:
    """Updates or adds an ingredient item in the Firestore pantry inventory.

    Args:
        item_name: Name of the pantry item (e.g. tofu, chickpeas).
        quantity: Quantity string (e.g. 400g, 2 cans).
        category: Ingredient category (e.g. protein, produce, grain).

    Returns:
        Confirmation message.
    """
    db = get_firestore_client()
    doc_id = item_name.lower().replace(" ", "_")
    item_data = {
        "item_name": item_name,
        "quantity": quantity,
        "category": category,
    }
    db.collection("pantry_items").document(doc_id).set(item_data)
    return f"Successfully updated pantry item '{item_name}' with quantity '{quantity}' in Firestore."


async def generate_dish_image(prompt: str, tool_context: ToolContext) -> str:
    """Generates an AI image of a dish using gemini-3.1-flash-lite-image in global region.

    Saves the image as an artifact in Playground and uploads it to public Cloud Storage,
    returning its public HTTPS URL.

    Args:
        prompt: Description of the dish to generate an image for (e.g. 'A delicious bowl of Mediterranean quinoa salad with feta').

    Returns:
        The public HTTP URL of the generated dish image.
    """
    try:
        client = genai.Client(
            vertexai=True, project=FIRESTORE_PROJECT_ID, location="global"
        )
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=f"Generate a realistic, appetizing food photo of: {prompt}",
        )
        image_bytes = None
        mime_type = "image/jpeg"
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if part.inline_data:
                    image_bytes = part.inline_data.data
                    if part.inline_data.mime_type:
                        mime_type = part.inline_data.mime_type
                    break

        if not image_bytes:
            return "Failed to generate dish image."

        ext = "png" if "png" in mime_type else "jpg"
        filename = f"dish_{uuid.uuid4().hex[:8]}.{ext}"

        # 1. Save artifact to Playground's Artifacts panel via ToolContext
        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        await tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload same image bytes to public Cloud Storage bucket
        storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        return f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{filename}"
    except Exception as e:
        return f"Error generating dish image: {str(e)}"


def fetch_online_recipes(ingredient_or_name: str) -> str:
    """Fetches real recipe ideas from TheMealDB public food database by meal name or main ingredient.

    Args:
        ingredient_or_name: Search query for meals (e.g. 'pasta', 'chicken', 'curry').

    Returns:
        A JSON string containing matching online recipes with ingredients and instructions.
    """
    api_key = os.getenv("THEMEALDB_API_KEY", "1")
    url = f"https://www.themealdb.com/api/json/v1/{api_key}/search.php?s={urllib.parse.quote(ingredient_or_name)}"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "SmartRecipeConcierge/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            meals = data.get("meals")
            if not meals:
                return f"No online recipes found matching '{ingredient_or_name}'."
            results = []
            for m in meals[:3]:  # Top 3 results
                ingredients = []
                for i in range(1, 21):
                    ing = m.get(f"strIngredient{i}")
                    measure = m.get(f"strMeasure{i}")
                    if ing and ing.strip():
                        ingredients.append(
                            f"{measure.strip() if measure else ''} {ing.strip()}".strip()
                        )
                results.append(
                    {
                        "title": m.get("strMeal"),
                        "category": m.get("strCategory"),
                        "cuisine": m.get("strArea"),
                        "instructions": m.get("strInstructions"),
                        "ingredients": ingredients,
                        "thumbnail": m.get("strMealThumb"),
                    }
                )
            return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error fetching online recipes: {str(e)}"


async def generate_recipe_video(prompt: str, tool_context: ToolContext) -> str:
    """Generates a short AI video of a recipe, dish, or cooking process using gemini-omni-flash-preview in the global region.

    Saves the video as an artifact in Playground and uploads it to public Cloud Storage,
    returning its public HTTPS URL.

    Args:
        prompt: Description of the cooking video clip to generate (e.g. 'Searing a steak in a hot skillet with herbs').

    Returns:
        The public HTTP URL of the generated recipe video.
    """
    try:
        import base64

        client = genai.Client(
            vertexai=True, project=FIRESTORE_PROJECT_ID, location="global"
        )
        video_bytes = None
        mime_type = "video/mp4"

        try:
            interaction = client.interactions.create(
                model="gemini-omni-flash-preview",
                input=f"A short continuous video clip showing: {prompt}",
                response_format={
                    "type": "video",
                    "resolution": "720p",
                    "aspect_ratio": "16:9",
                },
            )
            if hasattr(interaction, "output_video") and hasattr(
                interaction.output_video, "data"
            ):
                data_val = interaction.output_video.data
                video_bytes = (
                    base64.b64decode(data_val)
                    if isinstance(data_val, str)
                    else data_val
                )
        except Exception:
            response = client.models.generate_content(
                model="gemini-omni-flash-preview",
                contents=f"Generate a short video of: {prompt}",
            )
            for candidate in getattr(response, "candidates", []):
                for part in candidate.content.parts:
                    if part.inline_data:
                        video_bytes = part.inline_data.data
                        if part.inline_data.mime_type:
                            mime_type = part.inline_data.mime_type
                        break

        if not video_bytes:
            return "Failed to generate recipe video."

        filename = f"recipe_video_{uuid.uuid4().hex[:8]}.mp4"

        # 1. Save artifact to Playground's Artifacts panel via ToolContext
        artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
        await tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload same video bytes to public Cloud Storage bucket
        storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type=mime_type)

        return f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{filename}"
    except Exception as e:
        return f"Error generating recipe video: {str(e)}"


# WRITE: Memory extraction callback at the end of each turn
async def generate_memories_callback(callback_context: CallbackContext):
    await callback_context.add_session_to_memory()
    return None


# Helper function to construct VertexAiMemoryBankService
def get_memory_service():
    """Returns a VertexAiMemoryBankService instance configured for this project."""
    return VertexAiMemoryBankService(
        project=FIRESTORE_PROJECT_ID,
        location="us-east1",
        agent_engine_id=MEMORY_BANK_ID,
    )


# A2UI Schema Manager setup with v0.8 and BasicCatalog
schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are the Smart Recipe & Pantry Concierge. You help home cooks discover recipes, "
        "manage pantry inventory, scale recipes dynamically, and plan delicious personalized meals. "
        "CRITICAL: Always remember, track, and strictly enforce all user food allergies, dietary restrictions, and health sensitivities "
        "mentioned across conversations. Never suggest, save, or retrieve recipes or ingredients that contain any of the user's allergic triggers. "
        "Use your Memory Bank (via PreloadMemoryTool) to recall stored allergies and preferences at the start of every session. "
        "Use your Firestore tools to search local recipes, update pantry items, and save recipes. "
        "Use `fetch_online_recipes` to search external global recipe databases like TheMealDB. "
        "When asked to calculate or scale recipe quantities or macros, write and run Python code in your sandbox executor. "
        "When asked to show or picture a dish, use the `generate_dish_image` tool to create a visual URL. "
        "When asked for a cooking video or video clip, use the `generate_recipe_video` tool to generate a short dish video URL."
    ),
    workflow_description="Analyze the request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)

root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=AgentEngineSandboxCodeExecutor(
        agent_engine_resource_name=get_agent_engine_resource_name()
    ),
    tools=[
        PreloadMemoryTool(),
        search_recipes,
        add_recipe,
        get_pantry_items,
        update_pantry_item,
        generate_dish_image,
        generate_recipe_video,
        fetch_online_recipes,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)

