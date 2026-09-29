# Copyright 2026 Google LLC
# Seed script for Smart Recipe & Pantry Concierge Firestore database

import subprocess
import json
from google.cloud import firestore
from google.oauth2.credentials import Credentials
import google.auth

PROJECT_ID = "qwiklabs-gcp-01-d9763d3935dc"

def get_firestore_client():
    try:
        token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
        if token:
            return firestore.Client(project=PROJECT_ID, credentials=Credentials(token))
    except Exception:
        pass
    return firestore.Client(project=PROJECT_ID)

def seed_database():
    print(f"Connecting to Firestore with project ID: {PROJECT_ID}")
    db = get_firestore_client()

    # Seed Recipes
    recipes_ref = db.collection("recipes")
    sample_recipes = [
        {
            "id": "recipe_1",
            "title": "Gluten-Free Veggie Stir-Fry",
            "cuisine": "Asian",
            "prep_time_mins": 20,
            "dietary_tags": ["vegan", "gluten-free"],
            "ingredients": ["tofu", "broccoli", "carrots", "tamari", "sesame oil"],
            "instructions": "Sauté tofu and veggies in sesame oil until tender. Add tamari and serve warm over rice.",
            "calories_per_serving": 320,
        },
        {
            "id": "recipe_2",
            "title": "Mediterranean Quinoa Bowl",
            "cuisine": "Mediterranean",
            "prep_time_mins": 15,
            "dietary_tags": ["vegetarian", "gluten-free"],
            "ingredients": ["quinoa", "cucumbers", "cherry tomatoes", "feta cheese", "olive oil", "lemon"],
            "instructions": "Combine cooked quinoa with chopped cucumber, tomatoes, and feta. Drizzle with olive oil and lemon juice.",
            "calories_per_serving": 410,
        },
        {
            "id": "recipe_3",
            "title": "Avocado Chickpea Salad",
            "cuisine": "American",
            "prep_time_mins": 10,
            "dietary_tags": ["vegan", "gluten-free", "dairy-free"],
            "ingredients": ["chickpeas", "avocado", "red onion", "cilantro", "lime juice"],
            "instructions": "Mash avocado slightly, mix with rinsed chickpeas, diced red onion, cilantro, and fresh lime juice.",
            "calories_per_serving": 350,
        },
    ]

    for recipe in sample_recipes:
        recipes_ref.document(recipe["id"]).set(recipe)
        print(f"Seeded recipe: {recipe['title']}")

    # Seed Pantry Items
    pantry_ref = db.collection("pantry_items")
    sample_pantry = [
        {"item_name": "tofu", "quantity": "400g", "category": "protein"},
        {"item_name": "quinoa", "quantity": "500g", "category": "grain"},
        {"item_name": "avocado", "quantity": "3 whole", "category": "produce"},
        {"item_name": "chickpeas", "quantity": "2 cans", "category": "pantry staple"},
        {"item_name": "olive oil", "quantity": "750ml", "category": "oils & condiments"},
    ]

    for item in sample_pantry:
        doc_id = item["item_name"].replace(" ", "_")
        pantry_ref.document(doc_id).set(item)
        print(f"Seeded pantry item: {item['item_name']} ({item['quantity']})")

    print("Firestore database seeding completed successfully!")

if __name__ == "__main__":
    seed_database()
