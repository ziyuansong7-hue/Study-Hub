from __future__ import annotations

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, select
from werkzeug.utils import secure_filename

from app.i18n import translate
from app.routes import bp
from app.routes.general import _active_course
from models import Concept, ConceptCandidate, Document, DocumentChunk
from services.content_service import ConceptDiscoveryService
from services.course_service import CourseService
from services.database import session_scope
from services.llm import configured_provider_name
from services.material_service import MATERIAL_TYPES, MaterialService, UnsupportedMaterialError


@bp.route("/materials", methods=["GET", "POST"])
def materials():
    if request.method == "POST":
        uploaded = request.files.get("material")
        try:
            course_id = int(request.form.get("course_id", ""))
        except ValueError:
            course_id = 0
        if not uploaded or not uploaded.filename:
            flash(translate("choose_file"), "danger")
        elif not course_id:
            flash(translate("choose_course"), "danger")
        else:
            try:
                with session_scope() as db_session:
                    document, created = MaterialService(db_session).import_bytes(
                        course_id,
                        secure_filename(uploaded.filename),
                        uploaded.read(),
                        source_category=request.form.get("source_category", "course_material"),
                        source_year=request.form.get("source_year", type=int),
                        material_type=request.form.get("material_type", "auto"),
                        analysis_mode=request.form.get("analysis_mode", "auto"),
                        coverage_enabled=request.form.get("coverage_enabled") == "on",
                    )
                    if document.status == "failed":
                        flash(document.error_message or translate("material_failed"), "danger")
                    else:
                        chunk_count = db_session.scalar(
                            select(func.count()).select_from(DocumentChunk).where(
                                DocumentChunk.document_id == document.id
                            )
                        ) or 0
                        flash(
                            translate("material_processed_summary", count=chunk_count)
                            if created else translate("material_duplicate"),
                            "success",
                        )
            except (UnsupportedMaterialError, ValueError) as exc:
                flash(str(exc), "danger")
        return redirect(url_for("main.materials", course_id=course_id or None))
    with session_scope() as db_session:
        courses = CourseService(db_session).list_active()
        active_course = _active_course(db_session)
        requested_course_id = request.args.get("course_id", type=int)
        selected_course = next((course for course in courses if course.id == requested_course_id), active_course)
        document_query = select(Document)
        if selected_course:
            document_query = document_query.where(Document.course_id == selected_course.id)
        documents = list(db_session.scalars(document_query.order_by(Document.created_at.desc())))
        document_ids = [document.id for document in documents]
        chunk_counts = dict(db_session.execute(
            select(DocumentChunk.document_id, func.count(DocumentChunk.id))
            .where(DocumentChunk.document_id.in_(document_ids or [-1]))
            .group_by(DocumentChunk.document_id)
        ).all())
        usable_chunk_counts = dict(db_session.execute(
            select(DocumentChunk.document_id, func.count(DocumentChunk.id))
            .where(
                DocumentChunk.document_id.in_(document_ids or [-1]),
                DocumentChunk.usable_for_generation.is_(True),
            )
            .group_by(DocumentChunk.document_id)
        ).all())
        candidate_counts = dict(db_session.execute(
            select(ConceptCandidate.document_id, func.count(ConceptCandidate.id))
            .where(ConceptCandidate.document_id.in_(document_ids or [-1]))
            .group_by(ConceptCandidate.document_id)
        ).all())
        selected_document_id = request.args.get("document_id", type=int)
        selected_document = next(
            (document for document in documents if document.id == selected_document_id),
            documents[0] if documents else None,
        )
        return render_template(
            "materials.html",
            courses=courses,
            selected_course=selected_course,
            selected_document=selected_document,
            documents=documents,
            document_stats={
                document.id: {
                    "chunks": chunk_counts.get(document.id, 0),
                    "usable_chunks": usable_chunk_counts.get(document.id, 0),
                    "candidates": candidate_counts.get(document.id, 0),
                }
                for document in documents
            },
            provider_name=configured_provider_name(),
        )


@bp.get("/materials/<int:document_id>/chunks")
def material_chunks(document_id: int):
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    query_text = request.args.get("q", "").strip()
    quality_filter = request.args.get("quality", "usable")
    if quality_filter not in {"usable", "excluded", "all"}:
        quality_filter = "usable"
    with session_scope() as db_session:
        document = db_session.get(Document, document_id)
        if not document:
            abort(404)
        query = select(DocumentChunk).where(DocumentChunk.document_id == document.id)
        count_query = select(func.count()).select_from(DocumentChunk).where(
            DocumentChunk.document_id == document.id
        )
        if quality_filter == "usable":
            query = query.where(DocumentChunk.usable_for_generation.is_(True))
            count_query = count_query.where(DocumentChunk.usable_for_generation.is_(True))
        elif quality_filter == "excluded":
            query = query.where(DocumentChunk.usable_for_generation.is_(False))
            count_query = count_query.where(DocumentChunk.usable_for_generation.is_(False))
        if query_text:
            pattern = f"%{query_text}%"
            query = query.where(DocumentChunk.content.ilike(pattern))
            count_query = count_query.where(DocumentChunk.content.ilike(pattern))
        total = db_session.scalar(count_query) or 0
        chunks = list(db_session.scalars(
            query.order_by(DocumentChunk.chunk_index).offset((page - 1) * per_page).limit(per_page)
        ))
        return render_template(
            "material_chunks.html",
            document=document,
            chunks=chunks,
            page=page,
            pages=max(1, (total + per_page - 1) // per_page),
            total=total,
            query_text=query_text,
            quality_filter=quality_filter,
        )


@bp.post("/materials/<int:document_id>/extract-concepts")
def extract_concepts(document_id: int):
    try:
        with session_scope() as db_session:
            created = ConceptDiscoveryService(db_session).extract(document_id)
            document = db_session.get(Document, document_id)
            course_id = document.course_id if document else None
        flash(translate("concepts_extracted", count=created), "success")
    except ValueError as exc:
        course_id = None
        flash(f"{translate('concept_error')}: {exc}", "danger")
    return redirect(url_for("main.concepts", course_id=course_id, document_id=document_id))


@bp.post("/materials/<int:document_id>/analysis-settings")
def material_analysis_settings(document_id: int):
    try:
        material_type = request.form.get("material_type", "auto")
        analysis_mode = request.form.get("analysis_mode", "quick")
        if material_type not in MATERIAL_TYPES or analysis_mode not in {"quick", "structured"}:
            raise ValueError("invalid material analysis settings")
        with session_scope() as db_session:
            document = db_session.get(Document, document_id)
            if not document:
                raise ValueError("document not found")
            document.material_type = material_type
            document.material_type_source = "auto" if material_type == "auto" else "user"
            document.analysis_mode = analysis_mode
            document.coverage_enabled = request.form.get("coverage_enabled") == "on"
            MaterialService(db_session).refresh_metadata(document.id)
            course_id = document.course_id
        flash(translate("material_settings_updated"), "success")
    except ValueError as exc:
        course_id = None
        flash(str(exc), "danger")
    return redirect(url_for("main.materials", course_id=course_id, document_id=document_id))


@bp.get("/concepts")
def concepts():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 24
    status = request.args.get("status", "pending")
    recommendation = request.args.get("recommendation", "all")
    chapter = request.args.get("chapter", "")
    query_text = request.args.get("q", "").strip()
    with session_scope() as db_session:
        courses = CourseService(db_session).list_active()
        active_course = _active_course(db_session)
        course_id = request.args.get("course_id", type=int) or (active_course.id if active_course else None)
        documents = list(db_session.scalars(
            select(Document)
            .where(Document.course_id == (course_id or -1))
            .order_by(Document.created_at.desc())
        ))
        document_id = request.args.get("document_id", type=int)
        if document_id and not any(document.id == document_id for document in documents):
            document_id = None
        base = select(ConceptCandidate).join(Document).where(Document.course_id == (course_id or -1))
        if document_id:
            base = base.where(ConceptCandidate.document_id == document_id)
        if status in {"pending", "confirmed", "rejected"}:
            base = base.where(ConceptCandidate.status == status)
        if recommendation in {"create", "merge", "rename", "ignore", "review"}:
            base = base.where(ConceptCandidate.recommended_action == recommendation)
        if chapter:
            base = base.where(ConceptCandidate.chapter == chapter)
        if query_text:
            base = base.where(ConceptCandidate.name.ilike(f"%{query_text}%"))
        total = db_session.scalar(select(func.count()).select_from(base.subquery())) or 0
        filtered_subquery = base.subquery()
        filtered_ids = list(db_session.scalars(select(filtered_subquery.c.id).limit(1000)))
        candidates = list(db_session.scalars(
            base.order_by(ConceptCandidate.created_at, ConceptCandidate.id)
            .offset((page - 1) * per_page).limit(per_page)
        ))
        chunk_ids = {
            chunk_id for candidate in candidates for chunk_id in (candidate.source_chunk_ids or [])[:3]
        }
        chunk_rows = list(db_session.scalars(
            select(DocumentChunk).where(DocumentChunk.id.in_(chunk_ids or {-1}))
        ))
        chunks_by_id = {chunk.id: chunk for chunk in chunk_rows}
        evidence = {
            candidate.id: [chunks_by_id[value] for value in (candidate.source_chunk_ids or [])[:3] if value in chunks_by_id]
            for candidate in candidates
        }
        existing_concepts = list(db_session.scalars(
            select(Concept).where(Concept.course_id == (course_id or -1)).order_by(Concept.name)
        ))
        chapters = list(db_session.scalars(
            select(ConceptCandidate.chapter)
            .join(Document)
            .where(Document.course_id == (course_id or -1), ConceptCandidate.chapter != "")
            .distinct().order_by(ConceptCandidate.chapter)
        ))
        selected_document = next((item for item in documents if item.id == document_id), None)
        return render_template(
            "concepts.html",
            courses=courses,
            selected_course_id=course_id,
            documents=documents,
            selected_document=selected_document,
            candidates=candidates,
            evidence=evidence,
            existing_concepts=existing_concepts,
            chapters=chapters,
            selected_status=status,
            selected_recommendation=recommendation,
            selected_chapter=chapter,
            query_text=query_text,
            page=page,
            pages=max(1, (total + per_page - 1) // per_page),
            total=total,
            filtered_ids=filtered_ids,
            configured_provider=configured_provider_name(),
        )


@bp.post("/concepts/bulk")
def bulk_concepts():
    payload = request.get_json(silent=True) if request.is_json else None
    action = str((payload or {}).get("action") or request.form.get("action", ""))
    raw_ids = (payload or {}).get("candidate_ids") if payload else request.form.getlist("candidate_ids")
    try:
        candidate_ids = list(dict.fromkeys(int(value) for value in (raw_ids or [])))
    except (TypeError, ValueError):
        candidate_ids = []
    if not candidate_ids:
        return jsonify(ok=False, error={"code": "invalid_request", "message": "select at least one candidate"}), 400
    try:
        with session_scope() as db_session:
            service = ConceptDiscoveryService(db_session)
            rows = list(db_session.scalars(
                select(ConceptCandidate).where(ConceptCandidate.id.in_(candidate_ids))
            ))
            if len(rows) != len(candidate_ids):
                raise ValueError("one or more candidates were not found")
            course_id = rows[0].document.course_id
            if any(row.document.course_id != course_id for row in rows):
                raise ValueError("selected candidates must belong to one course")
            if action == "verify":
                changed = service.verify(candidate_ids)
            elif action in {"confirm", "reject"}:
                for row in rows:
                    merge_id = row.suggested_merge_concept_id if action == "confirm" and row.recommended_action == "merge" else None
                    service.decide(row.id, action=action, name=row.name, merge_concept_id=merge_id)
                changed = len(rows)
            elif action == "importance":
                value = str((payload or {}).get("value") or request.form.get("value", "supporting"))
                if value not in {"core", "supporting", "optional"}:
                    raise ValueError("invalid importance")
                for row in rows:
                    row.importance = value
                changed = len(rows)
            elif action == "chapter":
                value = str((payload or {}).get("value") or request.form.get("value", "")).strip()[:255]
                for row in rows:
                    row.chapter = value
                changed = len(rows)
            elif action == "merge":
                merged_name = str((payload or {}).get("value") or request.form.get("value", "")).strip()
                if not merged_name:
                    raise ValueError("merged concept name is required")
                target = db_session.scalar(select(Concept).where(
                    Concept.course_id == course_id, func.lower(Concept.name) == merged_name.casefold()
                ))
                if not target:
                    first = service.decide(rows[0].id, action="confirm", name=merged_name)
                    target = first.confirmed_concept
                for row in rows:
                    if row.status != "confirmed":
                        service.decide(row.id, action="confirm", merge_concept_id=target.id)
                changed = len(rows)
            else:
                raise ValueError("unknown bulk action")
        if request.is_json:
            return jsonify(ok=True, changed=changed)
        flash(translate("concepts_bulk_updated", count=changed), "success")
        return redirect(url_for("main.concepts", course_id=course_id))
    except ValueError as exc:
        if request.is_json:
            return jsonify(ok=False, error={"code": "invalid_request", "message": str(exc)}), 400
        flash(f"{translate('concept_error')}: {exc}", "danger")
        return redirect(url_for("main.concepts"))


@bp.post("/materials/<int:document_id>/reprocess")
def reprocess_material(document_id: int):
    try:
        with session_scope() as db_session:
            document = MaterialService(db_session).reprocess(document_id)
            course_id = document.course_id
        flash(translate("material_processed"), "success")
    except ValueError as exc:
        course_id = None
        flash(str(exc), "danger")
    return redirect(url_for("main.materials", course_id=course_id))

@bp.post("/materials/<int:document_id>/delete")
def delete_material(document_id: int):
    try:
        with session_scope() as db_session:
            document = db_session.get(Document, document_id)
            if not document:
                raise ValueError("document not found")
            course_id = document.course_id
            db_session.delete(document)
        flash(translate("material_deleted"), "success")
    except ValueError as exc:
        course_id = None
        flash(str(exc), "danger")
    return redirect(url_for("main.materials", course_id=course_id))
