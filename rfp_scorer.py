"""
ClimateView RFP Relevance Scoring Engine v1.1
Evaluates RFPs against ClimateView's product capabilities and strategic fit.

Changes from v1.0:
- Fix #13: ClearPath removed from competitor signals (it's a ClimateView partner)
- Fix #10/#11/#14: Competitor recommendation engine with severity tiers
- Fix #15: Score confidence based on input text length
- Fix #19: Platform vs. consulting detection
- Fix #21: Framework agreement multipliers actually applied
- Fix #8: Auto-expire logic for stale records
- Fix #9: Human-readable competitor signal explanations
"""

import json
import re
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional


def load_config(path="rfp_scoring_config.json"):
    with open(path) as f:
        return json.load(f)


@dataclass
class RFPInput:
    title: str
    issuing_entity: str
    description: str
    country: str
    budget_eur: Optional[float] = None
    budget_currency: Optional[str] = None  # Original currency if not EUR
    budget_period: Optional[str] = None  # "annual", "total", "unknown"
    deadline: Optional[str] = None  # ISO format YYYY-MM-DD
    source_portal: Optional[str] = None
    source_url: Optional[str] = None
    cpv_codes: list = field(default_factory=list)
    full_text: Optional[str] = None  # Full RFP text if available


# Currency conversion rates (approximate, updated periodically)
CURRENCY_TO_EUR = {
    'EUR': 1.0, 'USD': 0.92, 'GBP': 1.17, 'CHF': 1.05,
    'SEK': 0.088, 'DKK': 0.134, 'NOK': 0.087, 'CAD': 0.68,
    'AUD': 0.60, 'NZD': 0.55, 'PLN': 0.23, 'CZK': 0.040,
}


@dataclass
class ScoringResult:
    rfp_title: str
    issuing_entity: str
    country: str
    qualified: bool
    disqualification_reason: Optional[str]
    relevance_score: float  # 0-100
    win_probability: str  # High / Medium / Low / Edge Case
    win_probability_color: str
    feature_alignment_score: float
    geographic_fit_score: float
    budget_fit_score: float
    timeline_score: float
    competitive_score: float
    strategic_value_score: float
    advisory_bonus: float
    feature_breakdown: dict
    competitor_signals: list
    competitor_recommendation: str  # NEW: actionable recommendation
    positive_signals: list
    edge_case_flags: list
    score_confidence: str  # NEW: "high" / "medium" / "low" based on input quality
    rfp_type: str  # NEW: "platform" / "consulting_with_platform" / "consulting_only" / "unknown"
    deadline: Optional[str]
    deadline_status: str  # NEW: "open" / "closing_soon" / "urgent" / "expired" / "unknown"
    budget_eur: Optional[float]
    source_url: Optional[str]
    source_portal: Optional[str]
    scoring_config_version: str  # NEW: tracks which config scored this
    scored_at: str


class RFPScorer:
    def __init__(self, config_path="rfp_scoring_config.json"):
        self.config = load_config(config_path)
        self.config_version = self.config.get("version", "unknown")
        self.qual = self.config["qualification_filters"]
        self.dims = self.config["scoring_dimensions"]
        self.thresholds = self.config["win_probability_thresholds"]

    def _text_corpus(self, rfp: RFPInput) -> str:
        parts = [rfp.title, rfp.issuing_entity, rfp.description]
        if rfp.full_text:
            parts.append(rfp.full_text)
        return " ".join(p for p in parts if p).lower()

    def _has_pattern(self, text: str, patterns: list) -> list:
        found = []
        for p in patterns:
            if p.lower() in text:
                found.append(p)
        return found

    def _detect_rfp_type(self, text: str) -> str:
        """Detect whether RFP is for platform, consulting+platform, or consulting only."""
        platform_signals = [
            'saas', 'software', 'platform', 'digital tool', 'cloud-software',
            'web-based', 'dashboard', 'online-tool', 'web-plattform',
            'digitale plattform', 'software-lösung', 'it-system'
        ]
        consulting_signals = [
            'consulting', 'beratung', 'gutachten', 'expertise',
            'advisory', 'study', 'studie', 'analysis only', 'assessment only',
            'technical assistance', 'fachliche begleitung'
        ]
        p_hits = self._has_pattern(text, platform_signals)
        c_hits = self._has_pattern(text, consulting_signals)
        if p_hits and c_hits:
            return "consulting_with_platform"
        elif p_hits:
            return "platform"
        elif c_hits:
            return "consulting_only"
        return "unknown"

    def _assess_score_confidence(self, rfp: RFPInput) -> str:
        """Assess scoring confidence based on input text quality."""
        total_len = len(rfp.title or '') + len(rfp.description or '') + len(rfp.full_text or '')
        if total_len > 2000:
            return "high"
        elif total_len > 500:
            return "medium"
        return "low"

    def _compute_deadline_status(self, rfp: RFPInput) -> str:
        if not rfp.deadline:
            return "unknown"
        try:
            deadline = datetime.strptime(rfp.deadline, "%Y-%m-%d")
        except ValueError:
            return "unknown"
        days_left = (deadline - datetime.now()).days
        if days_left < 0:
            return "expired"
        elif days_left < 7:
            return "urgent"
        elif days_left < 21:
            return "closing_soon"
        return "open"

    def _convert_budget_to_eur(self, rfp: RFPInput) -> Optional[float]:
        """Convert budget to EUR if currency is specified."""
        if rfp.budget_eur is not None:
            # If currency specified and not EUR, convert
            if rfp.budget_currency and rfp.budget_currency.upper() != 'EUR':
                rate = CURRENCY_TO_EUR.get(rfp.budget_currency.upper(), 1.0)
                return rfp.budget_eur * rate
            return rfp.budget_eur
        return None

    def _qualify(self, rfp: RFPInput, text: str) -> tuple:
        """Returns (qualified: bool, reason: str|None, edge_flags: list)"""
        edge_flags = []

        # Check disqualification signals
        disqual = self._has_pattern(text, self.qual["disqualification_signals"])
        if len(disqual) >= 2:
            return False, f"Disqualification signals: {', '.join(disqual)}", []

        # Check client type
        client_qual = self._has_pattern(text, self.qual["client_type"]["qualifying_patterns"])
        client_edge = self._has_pattern(text, self.qual["client_type"]["edge_case_patterns"])
        client_disqual = self._has_pattern(text, self.qual["client_type"]["disqualifying_patterns"])
        if client_disqual and not client_qual:
            return False, f"Client type disqualified: {', '.join(client_disqual)}", []
        if not client_qual and not client_edge:
            return False, "No qualifying client type detected", []
        if client_edge and not client_qual:
            edge_flags.append(f"Edge case client type: {', '.join(client_edge)}")

        # Check subject matter
        subject_qual = self._has_pattern(text, self.qual["subject_matter"]["qualifying_patterns"])
        if not subject_qual:
            return False, "No qualifying subject matter detected", []

        # Check geographic scope
        country = rfp.country.upper() if rfp.country else ""
        primary_markets = [c.upper() for c in self.qual["geographic_scope"]["primary_markets"]]
        adjacent_markets = [c.upper() for c in self.qual["geographic_scope"]["adjacent_markets"]]
        if country not in primary_markets and country not in adjacent_markets:
            edge_flags.append(f"Non-target market: {rfp.country}")

        # Single disqualification signal is a warning
        if len(disqual) == 1:
            edge_flags.append(f"Minor disqualification signal: {disqual[0]}")

        return True, None, edge_flags

    def _score_feature_alignment(self, text: str) -> tuple:
        fa = self.dims["feature_alignment"]["functional_areas"]
        total_max = 0
        total_earned = 0
        breakdown = {}
        for area_name, area_cfg in fa.items():
            max_pts = area_cfg["max_points"]
            total_max += max_pts
            strong_found = self._has_pattern(text, area_cfg["strong_keywords"])
            moderate_found = self._has_pattern(text, area_cfg["moderate_keywords"])
            raw = len(strong_found) * 3 + len(moderate_found) * 1
            earned = min(raw, max_pts)
            total_earned += earned
            breakdown[area_name] = {
                "score": earned,
                "max": max_pts,
                "strong_matches": strong_found,
                "moderate_matches": moderate_found
            }
        score = (total_earned / total_max * 100) if total_max > 0 else 0
        return score, breakdown

    def _score_geographic_fit(self, rfp: RFPInput) -> float:
        geo = self.dims["geographic_fit"]
        country = rfp.country.upper() if rfp.country else ""
        primary_markets = [c.upper() for c in self.qual["geographic_scope"]["primary_markets"]]
        adjacent_markets = [c.upper() for c in self.qual["geographic_scope"]["adjacent_markets"]]
        if country in primary_markets:
            return geo["primary_score"]
        elif country in adjacent_markets:
            return geo["adjacent_score"]
        return geo["other_score"]

    def _score_budget_fit(self, budget_eur: Optional[float]) -> float:
        cfg = self.dims["budget_fit"]
        if budget_eur is None:
            return 50  # Unknown = neutral, don't penalize
        b = budget_eur
        if b < cfg["too_small_eur"]:
            return 10
        if b < cfg["acceptable_min_eur"]:
            return 30
        if cfg["sweet_spot_min_eur"] <= b <= cfg["sweet_spot_max_eur"]:
            return 100
        if b > cfg["sweet_spot_max_eur"]:
            return 80
        ratio = (b - cfg["acceptable_min_eur"]) / (cfg["sweet_spot_min_eur"] - cfg["acceptable_min_eur"])
        return 30 + ratio * 70

    def _score_timeline(self, rfp: RFPInput) -> float:
        cfg = self.dims["timeline_feasibility"]
        if not rfp.deadline:
            return 60  # Unknown = moderate
        try:
            deadline = datetime.strptime(rfp.deadline, "%Y-%m-%d")
        except ValueError:
            return 60
        days_left = (deadline - datetime.now()).days
        if days_left < 0:
            return 0
        if days_left < cfg["minimum_days_from_now"]:
            return 15
        if days_left >= cfg["ideal_days_from_now"]:
            return 100
        ratio = (days_left - cfg["minimum_days_from_now"]) / (cfg["ideal_days_from_now"] - cfg["minimum_days_from_now"])
        return 15 + ratio * 85

    def _score_competitive_landscape(self, text: str) -> tuple:
        """Returns (score, competitor_hits list, positive_hits list, recommendation str)"""
        cfg = self.dims["competitive_landscape"]["competitor_signals"]
        competitor_hits = []
        for label, patterns in cfg.items():
            if label.startswith("_") or label == "positive_signals":
                continue
            found = self._has_pattern(text, patterns)
            if found:
                competitor_hits.extend([(label, kw) for kw in found])

        positive_hits = self._has_pattern(text, cfg.get("positive_signals", []))

        # Score: start at 70, adjust
        score = 70
        score -= len(competitor_hits) * 10
        score += len(positive_hits) * 15
        score = max(0, min(100, score))

        # Generate recommendation based on severity
        recommendation = self._generate_competitor_recommendation(competitor_hits, positive_hits)

        return score, competitor_hits, positive_hits, recommendation

    def _generate_competitor_recommendation(self, competitor_hits: list, positive_hits: list) -> str:
        """Generate actionable recommendation based on competitive signals."""
        if not competitor_hits:
            if positive_hits:
                return "Open competition with positive signals. Strong position to bid."
            return "No competitor signals detected. Neutral competitive landscape."

        # Count by competitor type
        by_type = {}
        for label, kw in competitor_hits:
            by_type.setdefault(label, []).append(kw)

        kausal_count = len(by_type.get('kausal_spec', []))
        enersis_count = len(by_type.get('enersis_spec', []))
        generic_count = len(by_type.get('generic_competitor', []))

        parts = []
        if kausal_count >= 5:
            parts.append(f"Strong Kausal-spec pattern ({kausal_count} signals: {', '.join(by_type['kausal_spec'][:3])}...). Specification likely written with Kausal in mind. Recommend: SKIP unless you can negotiate scope changes or partner.")
        elif kausal_count >= 2:
            parts.append(f"Moderate Kausal signals ({kausal_count}). Some requirements favor open-source architecture but may be negotiable. Recommend: BID with clarification questions about mandatory vs. preferred requirements.")
        elif kausal_count == 1:
            parts.append(f"Single Kausal-adjacent signal ({by_type['kausal_spec'][0]}). Likely generic requirement, not competitor-specific. Recommend: BID normally.")

        if enersis_count >= 2:
            parts.append(f"GIS/utility focus ({enersis_count} signals). Core scope may be outside ClimateView's domain. Recommend: SKIP or bid for climate planning sub-scope only.")
        elif enersis_count == 1:
            parts.append(f"Minor GIS mention. Can be addressed with integration approach.")

        if generic_count > 0:
            parts.append(f"Incumbent/migration signals detected. May require displacement strategy.")

        if positive_hits:
            parts.append(f"Counterbalancing positive signals: {', '.join(positive_hits)}.")

        return " ".join(parts) if parts else "Mixed signals. Review manually."

    def _score_strategic_value(self, text: str) -> float:
        cfg = self.dims["strategic_value"]
        high = self._has_pattern(text, cfg["high_value_indicators"])
        medium = self._has_pattern(text, cfg["medium_value_indicators"])

        # Population detection
        pop_match = re.search(r'(\d[\d,]*)\s*(residents|population|inhabitants|einwohner)', text)
        pop_bonus = 0
        if pop_match:
            pop_str = pop_match.group(1).replace(",", "")
            try:
                pop = int(pop_str)
                if pop >= 500000:
                    pop_bonus = 25
                elif pop >= 100000:
                    pop_bonus = 15
                elif pop >= 50000:
                    pop_bonus = 8
            except ValueError:
                pass

        # Multi-entity detection
        multi_match = re.search(r'(\d+)\s+\w*\s*(local authorities|municipalities|kommunen|cities|gemeinden|councils|authorities|verwaltungen)', text)
        multi_bonus = 0
        if multi_match:
            try:
                n = int(multi_match.group(1))
                if n >= 20:
                    multi_bonus = 30
                elif n >= 5:
                    multi_bonus = 20
                elif n >= 2:
                    multi_bonus = 10
            except ValueError:
                pass

        base_score = len(high) * 15 + len(medium) * 8 + pop_bonus + multi_bonus

        # FIX #21: Apply multipliers for framework agreements, national scope, etc.
        multiplier = 1.0
        multiplier_cfg = cfg.get("multipliers", {})
        if any(kw in high for kw in ["framework agreement", "rahmenvertrag", "rahmenvereinbarung"]):
            multiplier = max(multiplier, multiplier_cfg.get("framework_agreement", 1.8))
        if any(kw in high for kw in ["national government", "national agency"]):
            multiplier = max(multiplier, multiplier_cfg.get("national_scope", 2.0))
        if any(kw in high for kw in ["multi-municipality"]):
            multiplier = max(multiplier, multiplier_cfg.get("multi_municipality", 1.5))

        return min(100, base_score * multiplier)

    def _score_advisory_bonus(self, text: str) -> float:
        cfg = self.config["advisory_service_bonus"]
        found = self._has_pattern(text, cfg["triggers"])
        if found:
            return min(cfg["bonus_points"], len(found) * 3)
        return 0

    def _determine_win_probability(self, score: float, edge_flags: list, competitor_hits: list) -> tuple:
        # FIX #14: Heavy competitor signals → Low, not Edge Case
        kausal_count = sum(1 for label, _ in competitor_hits if label == 'kausal_spec')
        if kausal_count >= 5:
            return "Low", "red"

        # FIX #11: Distinguish single vs. multiple signals for Edge Case
        if competitor_hits and len(competitor_hits) >= 2 and score >= 30:
            if edge_flags:
                return "Edge Case", "gray"
            # Multiple competitor signals but no other edge flags: still downgrade
            if score >= 70:
                return "Medium", "yellow"  # Downgrade from High

        th = self.thresholds
        if score >= th["high"]["min_score"]:
            return th["high"]["label"], th["high"]["color"]
        elif score >= th["medium"]["min_score"]:
            return th["medium"]["label"], th["medium"]["color"]
        else:
            return th["low"]["label"], th["low"]["color"]

    def score(self, rfp: RFPInput) -> ScoringResult:
        text = self._text_corpus(rfp)
        rfp_type = self._detect_rfp_type(text)
        score_confidence = self._assess_score_confidence(rfp)
        deadline_status = self._compute_deadline_status(rfp)
        budget_eur = self._convert_budget_to_eur(rfp)

        # Step 1: Qualification
        qualified, disqual_reason, edge_flags = self._qualify(rfp, text)

        if not qualified:
            return ScoringResult(
                rfp_title=rfp.title, issuing_entity=rfp.issuing_entity, country=rfp.country,
                qualified=False, disqualification_reason=disqual_reason,
                relevance_score=0, win_probability="N/A", win_probability_color="gray",
                feature_alignment_score=0, geographic_fit_score=0, budget_fit_score=0,
                timeline_score=0, competitive_score=0, strategic_value_score=0, advisory_bonus=0,
                feature_breakdown={}, competitor_signals=[], competitor_recommendation="",
                positive_signals=[], edge_case_flags=edge_flags,
                score_confidence=score_confidence, rfp_type=rfp_type,
                deadline=rfp.deadline, deadline_status=deadline_status,
                budget_eur=budget_eur, source_url=rfp.source_url, source_portal=rfp.source_portal,
                scoring_config_version=self.config_version, scored_at=datetime.now().isoformat()
            )

        # Step 2: Score each dimension
        fa_score, fa_breakdown = self._score_feature_alignment(text)
        geo_score = self._score_geographic_fit(rfp)
        budget_score = self._score_budget_fit(budget_eur)
        timeline_score = self._score_timeline(rfp)
        comp_score, comp_signals, pos_signals, comp_recommendation = self._score_competitive_landscape(text)
        strat_score = self._score_strategic_value(text)
        advisory_bonus = self._score_advisory_bonus(text)

        # RFP type adjustment: consulting_only gets a penalty
        type_adjustment = 0
        if rfp_type == "consulting_only":
            type_adjustment = -5
            edge_flags.append("Consulting-only RFP – no platform requirement detected. ClimateView may fit as sub-component.")
        elif rfp_type == "consulting_with_platform":
            type_adjustment = 2
        elif rfp_type == "platform":
            type_adjustment = 3

        # Step 3: Weighted composite
        weights = self.dims
        composite = (
            fa_score * weights["feature_alignment"]["weight"] +
            geo_score * weights["geographic_fit"]["weight"] +
            budget_score * weights["budget_fit"]["weight"] +
            timeline_score * weights["timeline_feasibility"]["weight"] +
            comp_score * weights["competitive_landscape"]["weight"] +
            strat_score * weights["strategic_value"]["weight"]
        )
        composite = min(100, composite + advisory_bonus + type_adjustment)

        # FIX #15: Add confidence note to edge flags
        if score_confidence == "low":
            edge_flags.append("Low confidence: score based on brief text only. Full document may score differently.")

        # Step 4: Win probability
        comp_signal_labels = [f"{label}: {kw}" for label, kw in comp_signals]
        win_prob, win_color = self._determine_win_probability(composite, edge_flags, comp_signals)

        return ScoringResult(
            rfp_title=rfp.title, issuing_entity=rfp.issuing_entity, country=rfp.country,
            qualified=True, disqualification_reason=None,
            relevance_score=round(composite, 1),
            win_probability=win_prob, win_probability_color=win_color,
            feature_alignment_score=round(fa_score, 1),
            geographic_fit_score=round(geo_score, 1),
            budget_fit_score=round(budget_score, 1),
            timeline_score=round(timeline_score, 1),
            competitive_score=round(comp_score, 1),
            strategic_value_score=round(strat_score, 1),
            advisory_bonus=round(advisory_bonus, 1),
            feature_breakdown=fa_breakdown,
            competitor_signals=comp_signal_labels,
            competitor_recommendation=comp_recommendation,
            positive_signals=pos_signals,
            edge_case_flags=edge_flags,
            score_confidence=score_confidence,
            rfp_type=rfp_type,
            deadline=rfp.deadline, deadline_status=deadline_status,
            budget_eur=budget_eur, source_url=rfp.source_url, source_portal=rfp.source_portal,
            scoring_config_version=self.config_version,
            scored_at=datetime.now().isoformat()
        )


def test_scorer():
    """Test with synthetic RFPs covering different scenarios."""
    scorer = RFPScorer()
    test_cases = [
        RFPInput(
            title="Climate Action Plan Development and GHG Inventory Platform",
            issuing_entity="City of Portland, Oregon",
            description="The City of Portland seeks proposals for a comprehensive SaaS platform to support development of its 2030 Climate Action Plan. Requirements include GHG inventory management compliant with GPC Protocol (Scopes 1-3), scenario modeling with backcasting capabilities, cost-benefit analysis of emission reduction measures, KPI monitoring dashboard, and multi-department collaboration tools. The platform should support transition planning with measurable targets and implementation tracking. Training for city staff included. Budget: $150,000 over 3 years.",
            country="US", budget_eur=140000, deadline="2026-04-15", source_portal="SAM.gov"
        ),
        RFPInput(
            title="Erstellung eines integrierten Klimaschutzkonzepts",
            issuing_entity="Landeshauptstadt Düsseldorf",
            description="Die Landeshauptstadt Düsseldorf schreibt die Erstellung eines integrierten Klimaschutzkonzepts aus. Gefordert werden: THG-Bilanzierung nach BISKO-Standard, Potenzialanalysen, Maßnahmenplanung mit Wirkungsabschätzung, Szenario-Entwicklung (BAU und Klimaneutralität 2035), sowie ein digitales Monitoring-Tool mit Dashboard zur Fortschrittskontrolle. Schulung der Klimaschutz-ManagerInnen ist Teil des Auftrags. Beratungsleistungen und Workshops sind erwünscht.",
            country="DE", budget_eur=200000, deadline="2026-03-30", source_portal="DTVP"
        ),
        RFPInput(
            title="Construction of Solar Panel Installation on Municipal Buildings",
            issuing_entity="Town of Springfield",
            description="The Town of Springfield seeks contractors for the design and construction of solar panel installations on 12 municipal buildings. Scope includes engineering design, permitting, procurement of panels, installation, and commissioning. Contractors must have 5+ years experience in commercial solar installation.",
            country="US", budget_eur=500000, deadline="2026-05-01", source_portal="BidNet"
        ),
        RFPInput(
            title="Net Zero Routemap for Scottish Local Authorities",
            issuing_entity="Scottish Government",
            description="The Scottish Government seeks a digital platform provider for a Climate Intelligence Service covering all 32 Scottish local authorities. Requirements: multi-municipality aggregation, GHG inventory per local authority, scenario modeling, transition planning, public-facing dashboards, and capacity building programme. The platform must support multi-level coordination between national and local government. Framework agreement for 4 years with option to extend. No incumbent vendor.",
            country="GB", budget_eur=350000, deadline="2026-05-30", source_portal="Public Contracts Scotland"
        ),
        RFPInput(
            title="Open Source Climate Data Platform",
            issuing_entity="City of Amsterdam",
            description="The City of Amsterdam seeks a self-hosted, open source climate data management platform. Requirements include CI/CD pipeline, Docker/Kubernetes deployment, full source code access, REST API for self-service integration, and on-premise deployment option. GraphQL endpoint required. The platform must support GHG inventory and basic reporting.",
            country="NL", budget_eur=80000, deadline="2026-04-20", source_portal="TenderNed"
        ),
    ]

    print("=" * 80)
    print("CLIMATEVIEW RFP SCORING ENGINE v1.1 – TEST RESULTS")
    print("=" * 80)

    for i, rfp in enumerate(test_cases, 1):
        result = scorer.score(rfp)
        print(f"\n{'─' * 70}")
        print(f"TEST {i}: {result.rfp_title[:60]}")
        print(f"Entity: {result.issuing_entity} ({result.country})")
        print(f"Qualified: {result.qualified}")
        if not result.qualified:
            print(f"Reason: {result.disqualification_reason}")
            continue
        print(f"Relevance Score: {result.relevance_score}/100 (confidence: {result.score_confidence})")
        print(f"Win Probability: {result.win_probability}")
        print(f"RFP Type: {result.rfp_type}")
        print(f"Deadline Status: {result.deadline_status}")
        print(f"  Feature Alignment: {result.feature_alignment_score}")
        print(f"  Geographic Fit:    {result.geographic_fit_score}")
        print(f"  Budget Fit:        {result.budget_fit_score}")
        print(f"  Timeline:          {result.timeline_score}")
        print(f"  Competitive:       {result.competitive_score}")
        print(f"  Strategic Value:   {result.strategic_value_score}")
        print(f"  Advisory Bonus:    {result.advisory_bonus}")
        if result.competitor_signals:
            print(f"  Competitor Signals: {result.competitor_signals}")
        print(f"  Recommendation: {result.competitor_recommendation}")
        if result.positive_signals:
            print(f"  Positive Signals: {result.positive_signals}")
        if result.edge_case_flags:
            print(f"  Edge Flags: {result.edge_case_flags}")

    return [scorer.score(rfp) for rfp in test_cases]


if __name__ == "__main__":
    test_scorer()
