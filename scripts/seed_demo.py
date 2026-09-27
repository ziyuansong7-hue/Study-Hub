from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from models import Concept, ConceptEdge, Course, Document, DocumentChunk, Question
from services.database import configure_database, init_database, session_scope

CONCEPTS = [
    ("logic-propositions", "Propositions and Truth Values"),
    ("logical-equivalence", "Logical Equivalence"),
    ("predicate-logic", "Predicate Logic"),
    ("proof-techniques", "Proof Techniques"),
    ("sets", "Sets and Set Operations"),
    ("relations", "Relations"),
    ("functions", "Functions"),
    ("induction", "Mathematical Induction"),
    ("recursion", "Recursive Definitions"),
    ("counting", "Counting Principles"),
    ("graphs", "Graph Fundamentals"),
    ("trees", "Trees"),
]

DEMO_MATERIAL = """# StudyHub CS245 Demo Notes
## Propositions [page 1]
A proposition is a declarative statement that is either true or false. A conjunction p AND q is true only when both operands are true.
## Logical Equivalence [page 2]
De Morgan's law states that NOT (p AND q) is equivalent to (NOT p) OR (NOT q).
## Predicate Logic [page 3]
A universal quantifier asserts a predicate for every element in its domain; an existential quantifier asserts at least one witness.
## Proof Techniques [page 4]
A direct proof starts from assumptions and derives the conclusion. A proof by contradiction assumes the negation of the claim.
## Sets [page 5]
Set intersection contains elements common to both sets; union contains elements in either set.
## Relations and Functions [page 6]
A relation is transitive when aRb and bRc imply aRc. A function maps each domain element to exactly one codomain element.
## Induction and Recursion [page 7]
Mathematical induction proves a base case and an inductive step. Recursive definitions specify base objects and construction rules.
## Counting [page 8]
The product rule multiplies the number of choices across sequential independent stages.
## Graphs and Trees [page 9]
A graph consists of vertices and edges. A tree is a connected acyclic graph.
"""

DEMO_RUBRICS = {
    "demo-q1": {"required_concepts": [{
        "name": "both p and q are true",
        "aliases": ["both p and q are true", "p and q are both true", "both are true"],
    }]},
    "demo-q7": {"required_concepts": [{
        "name": "exactly one",
        "aliases": ["exactly one", "one and only one", "only one", "1"],
    }]},
    "demo-q8": {"required_concepts": [
        {
            "name": "base case",
            "aliases": ["base case", "basis case", "base step", "basis step"],
        },
        {
            "name": "inductive step",
            "aliases": ["inductive step", "induction step", "inductive case", "induction case"],
        },
    ]},
    "demo-q10": {"required_concepts": [
        {"name": "connected", "aliases": ["connected"]},
        {"name": "acyclic", "aliases": ["acyclic", "has no cycles", "without cycles"]},
    ]},
}


def seed_demo(database_url: str = "sqlite:///instance/studyhub.db") -> dict[str, int]:
    configure_database(database_url)
    init_database()
    with session_scope() as session:
        course = session.scalar(select(Course).where(Course.code == "CS245"))
        if not course:
            course = Course(
                code="CS245",
                title="CS245 Demo Course",
                description="Synthetic demo data; not an official or complete course syllabus.",
                is_demo=True,
            )
            session.add(course)
            session.flush()

        concepts: dict[str, Concept] = {}
        for slug, name in CONCEPTS:
            concept = session.scalar(
                select(Concept).where(Concept.course_id == course.id, Concept.slug == slug)
            )
            if not concept:
                concept = Concept(
                    course_id=course.id, slug=slug, name=name,
                    description=f"Synthetic demo concept: {name}", is_demo=True,
                )
                session.add(concept)
                session.flush()
            concepts[slug] = concept

        edges = [
            ("logic-propositions", "logical-equivalence"),
            ("logic-propositions", "predicate-logic"),
            ("logical-equivalence", "proof-techniques"),
            ("sets", "relations"), ("sets", "functions"),
            ("proof-techniques", "induction"), ("induction", "recursion"),
            ("counting", "graphs"), ("graphs", "trees"),
        ]
        for pre, dep in edges:
            exists = session.scalar(select(ConceptEdge).where(
                ConceptEdge.prerequisite_id == concepts[pre].id,
                ConceptEdge.dependent_id == concepts[dep].id,
            ))
            if not exists:
                session.add(ConceptEdge(prerequisite_id=concepts[pre].id, dependent_id=concepts[dep].id))

        digest = hashlib.sha256(DEMO_MATERIAL.encode()).hexdigest()
        document = session.scalar(select(Document).where(
            Document.course_id == course.id, Document.content_hash == digest
        ))
        if not document:
            document = Document(
                course_id=course.id, filename="cs245_demo_notes.md", content_hash=digest,
                status="processed", source_category="course_material", is_demo=True,
            )
            session.add(document)
            session.flush()
            sections = [part.strip() for part in DEMO_MATERIAL.split("## ") if "[page " in part]
            for index, section_text in enumerate(sections):
                heading, body = section_text.split("\n", 1)
                page = int(heading.split("[page ")[1].split("]")[0])
                title = heading.split(" [page")[0]
                session.add(DocumentChunk(
                    document_id=document.id, chunk_index=index, content=body.strip(), page=page, section=title
                ))
        elif document.is_demo:
            document.source_category = "course_material"

        questions = [
            ("demo-q1", "logic-propositions", "When is p AND q true?", "when both p and q are true", "conjunction"),
            ("demo-q2", "logical-equivalence", "Rewrite NOT (p AND q) using De Morgan's law.", "(not p) or (not q)", "de morgan"),
            ("demo-q3", "predicate-logic", "What does a universal quantifier assert?", "a predicate holds for every element in its domain", "universal quantifier"),
            ("demo-q4", "proof-techniques", "What assumption begins a proof by contradiction?", "the negation of the claim", "proof contradiction"),
            ("demo-q5", "sets", "What is the intersection of two sets?", "elements common to both sets", "intersection"),
            ("demo-q6", "relations", "State the transitive property of a relation.", "if aRb and bRc then aRc", "transitive relation"),
            ("demo-q7", "functions", "How many outputs may a function assign to one input?", "exactly one", "function domain"),
            ("demo-q8", "induction", "Name the two main parts of an induction proof.", "base case and inductive step", "induction"),
            ("demo-q9", "counting", "Which rule combines choices across sequential stages?", "the product rule", "product rule"),
            ("demo-q10", "trees", "Give the defining graph properties of a tree.", "connected and acyclic", "tree graph"),
        ]
        for external_id, slug, prompt, answer, query in questions:
            existing_question = session.scalar(select(Question).where(Question.external_id == external_id))
            if not existing_question:
                session.add(Question(
                    course_id=course.id, concept_id=concepts[slug].id, external_id=external_id,
                    prompt=prompt, expected_answer=answer,
                    rubric=DEMO_RUBRICS.get(external_id, {"accepted_answers": [answer]}),
                    difficulty=0.4, evidence_query=query, source_type="demo",
                    question_type="short_answer", difficulty_level=2, difficulty_score=35,
                    difficulty_profile={
                        "conceptual_depth": 1, "reasoning_steps": 1, "topic_integration": 0,
                        "novelty": 1, "calculation_load": 0, "prerequisite_breadth": 1,
                    }, expected_minutes=2, difficulty_rationale="Synthetic recall-level demo question.",
                    moderation_status="approved", is_demo=True,
                ))
            elif existing_question.is_demo:
                existing_question.source_type = "demo"
                existing_question.question_type = "short_answer"
                existing_question.rubric = DEMO_RUBRICS.get(
                    external_id, {"accepted_answers": [answer]}
                )
                existing_question.moderation_status = "approved"
                existing_question.is_active = True
        session.flush()
        return {
            "courses": 1,
            "concepts": len(concepts),
            "questions": len(questions),
            "documents": 1,
        }


if __name__ == "__main__":
    result = seed_demo(os.getenv("STUDYHUB_DATABASE_URL", "sqlite:///instance/studyhub.db"))
    print("Seeded idempotent demo data:", result)
    print("NOTE: demo content is synthetic and does not claim to cover the real CS245 syllabus.")
