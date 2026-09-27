import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ai.analyst.context_builder import build_context
from app.ai.analyst.prompts import PROMPT_VERSION, SYSTEM_PROMPT, user_prompt
from app.ai.analyst.schemas import AnalystOutput
from app.ai.analyst.validator import InvalidAnalysis, validate_output
from app.ai.providers.base import LLMError, LLMProvider
from app.ai.providers.ollama import OllamaProvider
from app.config import settings
from app.models.analysis import AIAnalysis
from app.models.company import Company
from app.models.financial import FinancialPeriod, FinancialStatus

logger = logging.getLogger(__name__)


def configured_provider() -> LLMProvider:
    return OllamaProvider(settings.ollama_base_url, settings.ollama_model,
                          settings.ollama_timeout_seconds, settings.ai_temperature)


def analyze_period(db: Session, company: Company, period: FinancialPeriod,
                   provider: LLMProvider | None = None, regenerate: bool = False) -> AIAnalysis:
    if period.company_id != company.id:
        raise ValueError("PERIOD_COMPANY_MISMATCH")
    if period.status != FinancialStatus.VALIDATED:
        raise ValueError("PERIOD_NOT_VALIDATED")
    require_extended = provider is None
    provider = provider or configured_provider()
    db.refresh(period, ["metrics"])
    context = build_context(db, company.name, company.symbol, period)
    if not context.fact_count:
        raise ValueError("NO_VERIFIED_FACTS")
    existing = db.scalar(select(AIAnalysis).where(AIAnalysis.financial_period_id == period.id,
        AIAnalysis.status == "SUCCESS", AIAnalysis.context_hash == context.hash,
        AIAnalysis.model_provider == provider.name, AIAnalysis.model_name == provider.model,
        AIAnalysis.prompt_version == PROMPT_VERSION).order_by(AIAnalysis.id.desc()))
    if existing and not regenerate:
        return existing

    record = AIAnalysis(company_id=company.id, financial_period_id=period.id,
        analysis_type="FINANCIAL_RESULTS", model_provider=provider.name,
        model_name=provider.model, prompt_version=PROMPT_VERSION, context_hash=context.hash,
        context_chars=len(context.text), evidence_registry=context.registry,
        status="PENDING", attempt_count=0)
    db.add(record)
    db.commit()
    db.refresh(record)
    logger.info("[ai] company=%s period=%s_%s scope=%s provider=%s model=%s facts=%s metrics=%s evidence_items=%s context_chars=%s",
                company.symbol, period.quarter or "FY", period.fiscal_year, period.scope.value,
                provider.name, provider.model, context.fact_count, context.metric_count,
                len(context.registry), len(context.text))
    feedback = None
    for attempt in (1, 2):
        record.attempt_count = attempt
        try:
            raw = provider.generate(SYSTEM_PROMPT, user_prompt(context.text, repair_feedback=feedback),
                                    AnalystOutput.model_json_schema())
            output = validate_output(raw, context.registry, period.period_end.year,
                                     require_extended=require_extended)
            record.status = "SUCCESS"
            record.summary = output.summary
            record.structured_output = output.model_dump()
            record.failure_reason = None
            break
        except InvalidAnalysis as exc:
            logger.warning("[ai] rejected_model_output reason=%s token=%s", exc.reason, exc.token)
            feedback = exc.reason + (f" (unsupported numeric token: {exc.token})" if exc.token else "")
            record.status = "INVALID"
            record.failure_reason = exc.reason
        except LLMError as exc:
            record.status = "FAILED"
            record.failure_reason = exc.reason
            break
        except Exception:
            logger.exception("[ai] unexpected provider failure")
            record.status = "FAILED"
            record.failure_reason = "ANALYSIS_ERROR"
            break
    db.commit()
    db.refresh(record)
    logger.info("[ai] company=%s period_id=%s analysis_status=%s reason=%s",
                company.symbol, period.id, record.status, record.failure_reason)
    return record
