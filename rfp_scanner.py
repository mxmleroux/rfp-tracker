"""
ClimateView RFP Scanner Agent v1.1
Scans procurement portals for relevant climate action RFPs.

Fixes from v1.0:
- Cross-portal deduplication
- Record update on re-scan
- Retry on timeout
- Atomic writes
- Auto-expire stale records
- Pagination support
"""

import json
import hashlib
import os
import sys
import time
import shutil
import tempfile
import logging
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('rfp_scanner')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(SCRIPT_DIR, 'rfp_data.json')
SCAN_LOG_FILE = os.path.join(SCRIPT_DIR, 'scan_log.json')

sys.path.insert(0, SCRIPT_DIR)
from rfp_scorer import RFPScorer, RFPInput

KEYWORDS = {
    # -------------------------------------------------------------------------
    # Wide-net keyword list. Ordered by priority (scanners slice from front).
    # Scorer qualification filters + disqualification signals remove noise.
    # -------------------------------------------------------------------------
    'en': [
        # === TIER 1: Highest-precision ClimateView core terms ===
        'climate action plan', 'greenhouse gas inventory', 'GHG emissions',
        'net-zero strategy', 'climate data platform', 'carbon accounting',
        'emissions reduction plan', 'climate software', 'sustainability reporting',
        'climate action', 'decarbonization', 'net zero',
        'carbon management', 'climate transition plan', 'climate roadmap',
        'net zero roadmap', 'municipal energy planning', 'energy transition plan',
        'climate intelligence', 'sustainability platform', 'carbon budget',
        'climate monitoring', 'transition planning', 'climate dashboard',
        'heat planning', 'heating plan', 'district heating strategy',
        'climate neutrality', 'carbon neutrality strategy',
        # === TIER 2: Climate action & planning variants ===
        'climate strategy', 'climate plan', 'climate programme', 'climate framework',
        'municipal climate plan', 'local climate action', 'city climate plan',
        'regional climate plan', 'climate action framework', 'climate master plan',
        'community climate plan', 'climate action roadmap', 'climate emergency plan',
        'climate emergency action', 'climate commitment', 'climate policy',
        'climate preparedness', 'national climate plan', 'state climate plan',
        'county climate plan', 'climate change plan', 'climate change strategy',
        'climate change action plan', 'climate change mitigation',
        # === TIER 2: Net zero & carbon neutral variants ===
        'net-zero strategy', 'net zero pathway', 'net zero target', 'net zero plan',
        'carbon neutral', 'carbon neutrality', 'carbon neutrality roadmap',
        'zero carbon', 'zero emission strategy', 'zero emissions',
        'net zero city', 'net zero municipality', 'net zero region',
        'carbon free', 'fossil free', 'post-carbon',
        # === TIER 2: GHG & emissions variants ===
        'GHG inventory', 'GHG accounting', 'emissions accounting',
        'emissions reduction', 'emissions tracking', 'emissions monitoring',
        'emissions reporting', 'carbon footprint', 'carbon reporting',
        'emissions baseline', 'carbon baseline', 'carbon disclosure',
        'scope 1 2 3 emissions', 'GHG management', 'carbon inventory',
        'emissions calculator', 'greenhouse gas reporting', 'greenhouse gas management',
        'emissions data', 'emissions database', 'carbon data',
        # === TIER 2: Decarbonization ===
        'decarbonisation', 'decarbonization strategy', 'decarbonization pathway',
        'decarbonization roadmap', 'deep decarbonization', 'sectoral decarbonization',
        'economy-wide decarbonization', 'urban decarbonization',
        # === TIER 2: Energy transition & planning ===
        'energy transition', 'energy planning', 'energy strategy',
        'renewable energy strategy', 'energy roadmap', 'clean energy plan',
        'clean energy transition', 'energy management', 'energy efficiency strategy',
        'local energy plan', 'district energy', 'energy system transformation',
        'municipal energy plan', 'energy master plan', 'energy action plan',
        'renewable energy plan', 'clean energy strategy',
        'integrated energy plan', 'urban energy planning',
        # === TIER 2: Heat planning ===
        'heat strategy', 'district heating', 'heat network',
        'heat decarbonization', 'heating decarbonization', 'heat pump strategy',
        'thermal energy plan', 'heat network strategy', 'heat transition',
        'district heating expansion', 'geothermal energy plan',
        # === TIER 2: Sustainability ===
        'sustainability strategy', 'sustainability management', 'sustainability plan',
        'sustainability framework', 'sustainability assessment',
        'sustainability monitoring', 'ESG reporting', 'ESG strategy',
        'environmental sustainability', 'sustainability data',
        'sustainability dashboard', 'sustainability transition',
        'sustainable development plan', 'sustainability indicators',
        'sustainability performance', 'corporate sustainability',
        # === TIER 2: Climate software / data / platform ===
        'climate platform', 'climate data', 'climate data platform',
        'climate tool', 'climate analytics', 'climate monitoring platform',
        'carbon management platform', 'carbon management software',
        'emissions management platform', 'sustainability software',
        'environmental data platform', 'climate information system',
        'carbon calculator', 'emissions calculator tool',
        'climate decision support', 'climate planning tool',
        # === TIER 2: Transition planning ===
        'transition plan', 'transition roadmap', 'transition strategy',
        'just transition', 'green transition', 'ecological transition',
        'climate transition', 'systemic transition', 'systemic change',
        'transformation plan', 'green deal',
        # === TIER 2: Monitoring & reporting ===
        'climate reporting', 'environmental reporting', 'environmental monitoring',
        'carbon monitoring', 'emissions monitoring system', 'climate tracking',
        'progress tracking', 'KPI monitoring', 'environmental performance',
        'performance monitoring', 'climate indicators', 'progress reporting',
        # === TIER 3: Standards & methodologies ===
        'science based targets', 'CDP reporting', 'GPC protocol',
        'covenant of mayors', 'SECAP', 'sustainable energy action plan',
        'climate risk assessment', 'climate vulnerability assessment',
        'climate impact assessment', 'global covenant of mayors',
        'TCFD reporting', 'CSRD compliance', 'EU taxonomy',
        'SDG reporting', 'Paris agreement alignment', 'COP commitments',
        # === TIER 3: Adaptation & resilience ===
        'climate adaptation', 'climate resilience', 'adaptation planning',
        'resilience strategy', 'climate risk', 'vulnerability assessment',
        'adaptation strategy', 'resilience planning', 'climate resilience plan',
        'urban heat island', 'flood resilience', 'urban resilience',
        'climate risk management', 'adaptation roadmap', 'adaptation framework',
        'nature-based solutions', 'green infrastructure',
        'climate-proof', 'climate proofing', 'resilient city',
        # === TIER 3: Sector-specific (buildings, transport, waste, industry) ===
        'building energy efficiency', 'building decarbonization',
        'building retrofit strategy', 'transport emissions reduction',
        'sustainable transport plan', 'sustainable mobility plan',
        'waste emissions reduction', 'circular economy strategy',
        'industrial decarbonization', 'land use emissions',
        'urban planning climate', 'green building strategy',
        'low carbon transport', 'fleet electrification',
        'zero emission vehicles', 'active mobility',
        # === TIER 3: Consulting / advisory ===
        'climate consulting', 'climate advisory', 'climate capacity building',
        'climate training', 'environmental consulting', 'sustainability consulting',
        'carbon consulting', 'climate technical assistance',
        'climate knowledge transfer', 'climate expertise',
        # === TIER 3: Urban / municipal ===
        'urban sustainability', 'smart city climate', 'green city',
        'sustainable city', 'climate resilient city', 'sustainable urban development',
        'liveable city', 'healthy city', 'inclusive city',
        # === TIER 3: Finance & funding ===
        'climate finance', 'green bonds', 'climate investment',
        'sustainable finance', 'climate funding', 'green finance',
        'climate budget', 'green investment', 'carbon pricing',
        'climate philanthropy', 'adaptation finance',
        # === TIER 3: Specific programs / frameworks ===
        'European Green Deal', 'Fit for 55', 'Green New Deal',
        'Race to Zero', 'C40 cities', 'ICLEI', 'ClearPath',
        '100 climate-neutral cities', 'EU Climate Pact',
        'climate emergency declaration', 'Global Covenant',
        # === TIER 3: Procurement framing ===
        'SaaS platform', 'software as a service', 'digital tool',
        'cloud platform', 'web-based platform', 'data analytics platform',
        'decision support system', 'management information system',
        'IT services environment', 'environmental IT',
    ],

    'de': [
        # === TIER 1: Klimaschutz-Kernfamilie ===
        'Klimaschutzkonzept', 'Klimaschutzstrategie', 'Klimaschutzfahrplan',
        'Klimaschutzmanagement', 'Klimaschutzmanager', 'Klimaschutzteilkonzept',
        'Klimaschutzprogramm', 'Klimaschutzplan', 'Klimaschutzmaßnahmen',
        'Klimaschutzcontrolling', 'Klimaschutzmonitoring', 'Klimaschutzberichterstattung',
        'Klimaschutzberatung', 'kommunaler Klimaschutz', 'integriertes Klimaschutzkonzept',
        'Klimaschutzbericht', 'Klimaschutzagentur', 'Klimaschutzleitbild',
        'Klimaschutzplanung', 'Klimaschutz-Dashboard', 'Klimaschutzinitiative',
        'Klimaschutzaktionsplan', 'Klimaschutzkoordination', 'Klimaschutzprojekt',
        'integriertes Klimaschutz- und Energiekonzept', 'Klimaschutzförderung',
        'Klimaschutz-Software', 'Klimaschutzvereinbarung',
        # === TIER 1: Wärmeplanung-Familie ===
        'kommunale Wärmeplanung', 'Wärmeplanung', 'Wärmeleitplanung',
        'Wärmeplanungsgesetz', 'Wärmeversorgungskonzept', 'Wärmenetzplanung',
        'Wärmestrategie', 'Wärmekataster', 'Wärmewende', 'Wärmeversorgung',
        'Wärmeatlas', 'Nahwärmekonzept', 'Fernwärmekonzept', 'Fernwärmeausbau',
        'Wärmenetz', 'Wärmekonzept', 'kommunale Wärmeversorgung',
        'klimaneutrale Wärmeversorgung', 'Wärmetransformation', 'dekarbonisierte Wärme',
        # === TIER 1: THG / CO2 / Emissionen ===
        'Treibhausgasbilanz', 'THG-Bilanz', 'CO2-Bilanz', 'CO2-Bilanzierung',
        'CO2-Neutralität', 'Treibhausgasneutralität', 'CO2-Monitoring',
        'CO2-Reduktion', 'CO2-Minderung', 'Emissionskataster', 'Emissionsbilanz',
        'Emissionsminderung', 'Emissionsreduktion', 'Emissionsberichterstattung',
        'CO2-Fußabdruck', 'Klimabilanz', 'Treibhausgasinventar',
        'Treibhausgasminderung', 'CO2-Budget', 'CO2-Berichterstattung',
        # === TIER 1: Klimaneutralität-Familie ===
        'Klimaneutralität', 'klimaneutrale Stadt', 'klimaneutrale Kommune',
        'Klimaneutralitätsstrategie', 'Klimaneutralitätspfad',
        'Klimaneutralitätskonzept', 'klimaneutrales Quartier',
        'Klimaneutralitätsziel', 'klimaneutral 2040', 'klimaneutral 2045',
        # === TIER 1: Energie-Familie ===
        'Energiekonzept', 'Energieleitplanung', 'Energiestrategie',
        'Energiemanagement', 'Energiebilanz', 'Energiemonitoring',
        'Energiewende', 'Energieversorgungskonzept', 'erneuerbare Energien',
        'Energieeffizienzstrategie', 'Energiebericht', 'Energieplanung',
        'Energienutzungsplan', 'kommunales Energiemanagement',
        'Energiewendestrategie', 'kommunale Energieplanung', 'Energiefahrplan',
        'Sektorenkopplung', 'integrierte Energieplanung',
        'Energie- und Klimaschutzkonzept',
        # === TIER 1: Nachhaltigkeit-Familie ===
        'Nachhaltigkeitsbericht', 'Nachhaltigkeitsmanagement',
        'Nachhaltigkeitsstrategie', 'Nachhaltigkeitskonzept',
        'Nachhaltigkeitsberichterstattung', 'Nachhaltigkeitsmonitoring',
        'kommunale Nachhaltigkeit', 'Nachhaltigkeitsindikatoren',
        'Nachhaltigkeitsbewertung', 'Nachhaltigkeitscontrolling',
        'Nachhaltigkeitsplattform', 'Nachhaltigkeitsdaten',
        'Nachhaltigkeitsdashboard', 'Nachhaltigkeitsprogramm',
        'kommunales Nachhaltigkeitsmanagement',
        # === TIER 2: Klima breit ===
        'Klimaanpassung', 'Klimaanpassungskonzept', 'Klimadaten',
        'Klimafolgenmanagement', 'Klimastrategie', 'Klimaplan',
        'Klimaprogramm', 'Klimanotstand', 'Klimafolgenabschätzung',
        'Klimaresilienz', 'Klimavorsorge', 'Klimarisikoanalyse',
        'Klimadatenplattform', 'Klimawandel Anpassung', 'Klimafolgenanpassung',
        'Klimaschutzgesetz', 'Klimawandelstrategie', 'Klimarisikovorsorge',
        # === TIER 2: Planung / Beratung ===
        'Potenzialanalyse', 'Maßnahmenplanung', 'Szenarioentwicklung',
        'Wirkungsabschätzung', 'Dekarbonisierung', 'Dekarbonisierungsstrategie',
        'Maßnahmenkatalog', 'Umsetzungsfahrplan', 'Handlungsfeld',
        'Handlungsempfehlung', 'Bestandsanalyse', 'Zielkonzept',
        'Machbarkeitsstudie Klimaschutz', 'Klimaschutzgutachten',
        'Szenarien Klimaneutralität', 'Referenzszenario', 'Zielszenario',
        # === TIER 2: Digital / Plattform ===
        'Monitoring-Tool', 'digitales Klimaschutzmanagement',
        'CO2-Rechner', 'Emissionsrechner', 'Klimaschutz-Monitoring-System',
        'Datenplattform Klimaschutz', 'digitale Klimaschutzplanung',
        'Klimaschutz-Plattform', 'Klimaschutz-Tool', 'Klimadaten-Software',
        'IT-Dienstleistung Klimaschutz', 'Software Klimaschutz',
        'SaaS Klimaschutz', 'Cloud-Plattform Klimaschutz',
        # === TIER 2: Standards ===
        'BISKO-Standard', 'BISKO', 'Bilanzierungssystematik', 'GPC-Protokoll',
        'kommunale Bilanzierung', 'BISKO-konforme Bilanz',
        # === TIER 2: Mobilität / Verkehr ===
        'Verkehrswende', 'nachhaltige Mobilität', 'Mobilitätswende',
        'Mobilitätskonzept', 'klimafreundliche Mobilität',
        'Verkehrsemissionen', 'emissionsfreier Verkehr', 'Radverkehrskonzept',
        'Elektromobilität', 'ÖPNV Dekarbonisierung',
        # === TIER 3: Adaptation / Resilience ===
        'Klimaanpassungsstrategie', 'Hitzeaktionsplan', 'Starkregenvorsorge',
        'Hochwasserschutzkonzept', 'Überflutungsvorsorge', 'Klimaresilienzstrategie',
        'Hitzeschutzplan', 'Stadtklima', 'Stadtklimaanalyse',
        'urbane Resilienz', 'Klimavulnerabilität', 'Klimarisikobewertung',
        'Klimaanpassungsmaßnahmen', 'Grünflächenstrategie',
        'Schwammstadt', 'blau-grüne Infrastruktur',
        # === TIER 3: Sektoren (Gebäude, Industrie, Abfall) ===
        'Gebäudesanierungsstrategie', 'Sanierungsfahrplan', 'Quartierskonzept',
        'energetische Quartiersentwicklung', 'Gebäudeenergiekonzept',
        'industrielle Dekarbonisierung', 'Abfallwirtschaftskonzept',
        'Kreislaufwirtschaftsstrategie', 'CO2-arme Industrie',
        'klimaneutrale Gebäude', 'Gebäudesektor Emissionen',
        # === TIER 3: Finanzierung / Förderung ===
        'Nationale Klimaschutzinitiative', 'NKI', 'Kommunalrichtlinie',
        'KfW Klimaschutz', 'Klimaschutzförderung', 'Fördermittel Klimaschutz',
        'Green Bonds', 'nachhaltige Finanzierung', 'Klimafinanzierung',
        'Förderprogramm Klimaschutz', 'EFRE Klimaschutz',
        # === TIER 3: Berichterstattung / Compliance ===
        'CSRD Berichterstattung', 'EU-Taxonomie', 'ESG-Berichterstattung',
        'SDG Berichterstattung', 'Klimaberichterstattung',
        'Nachhaltigkeits-Reporting', 'Umweltberichterstattung',
        # === TIER 3: Vergabe-Framing ===
        'Beratungsleistung Klimaschutz', 'Dienstleistung Klimaschutz',
        'IT-Vergabe Klimaschutz', 'Softwarebeschaffung Umwelt',
        'Rahmenvereinbarung Klimaschutz', 'Konzepterstellung Klimaschutz',
        'Gutachten Klimaschutz', 'Studie Klimaschutz',
        # === TIER 3: Specific programs ===
        'European Green Deal', 'Fit for 55', '100 klimaneutrale Städte',
        'Klimapakt', 'Konvent der Bürgermeister', 'Masterplan 100% Klimaschutz',
        'Klimaschutz Masterplan', 'klimaneutrale Verwaltung',
        # === TIER 3: Landwirtschaft / Landnutzung ===
        'Landnutzungsemissionen', 'klimafreundliche Landwirtschaft',
        'Flächennutzungsplanung Klimaschutz', 'Moorschutz',
        'Kohlenstoffsenke', 'LULUCF',
        # === TIER 3: Stadtentwicklung / Quartier ===
        'Stadtentwicklungskonzept', 'integriertes Stadtentwicklungskonzept',
        'klimagerechte Stadtentwicklung', 'nachhaltige Stadtentwicklung',
        'Quartiersentwicklung', 'Quartiersversorgung', 'Quartierslösung',
        'energetische Stadtsanierung', 'Städtebauförderung',
        'kommunales Flächenmanagement',
        # === TIER 3: Stadtwerke / Versorgung ===
        'Stadtwerke Dekarbonisierung', 'Versorgungskonzept',
        'Fernwärmestrategie', 'Nahwärmestrategie',
        'Abwärmenutzung', 'Power-to-Heat', 'Wärmespeicher',
        'Geothermie', 'Solarthermie', 'Biomasse Wärme',
        # === TIER 3: Weitere Vergabe / Procurement ===
        'Ausschreibung Klimaschutz', 'Vergabe Klimaschutz',
        'öffentliche Ausschreibung', 'Leistungsverzeichnis',
        'Konzepterstellung', 'Fachgutachten',
        'Strategieberatung Klimaschutz', 'Prozessbegleitung',
        'Beteiligungsprozess Klimaschutz', 'Akteursbeteiligung',
        # === TIER 3: IT / Digital erweitert ===
        'Geoinformationssystem Klimaschutz', 'GIS Klimadaten',
        'Datenmanagement Emissionen', 'Webplattform Klimaschutz',
        'Dashboard Klimaschutz', 'Berichtsplattform',
        'automatisierte Bilanzierung', 'digitale Wärmeplanung',
        # === TIER 3: Bundesländer / Regionale Programme ===
        'Landesklimaschutzgesetz', 'Landesklimaplan',
        'Regionaler Klimaschutzplan', 'Kreisklimaschutzkonzept',
        'Klimaschutzagentur', 'Zukunftsstadt',
    ],

    'fr': [
        # === Plans & stratégies ===
        'plan climat', 'PCAET', 'plan climat air énergie territorial',
        'plan climat air énergie', 'bilan carbone', 'bilan GES',
        'inventaire GES', 'stratégie bas-carbone', 'stratégie bas carbone',
        'transition écologique', 'neutralité carbone', 'plan énergie climat',
        'bilan GES territorial', 'stratégie climat', 'feuille de route climat',
        'plan action climatique', 'objectif zéro émission',
        'stratégie de transition', 'planification climatique',
        'plan de transition', 'trajectoire bas carbone',
        # === Collectivités ===
        'ville neutre en carbone', 'collectivité neutre en carbone',
        'commune neutre en carbone', 'territoire neutre en carbone',
        'plan climat territorial', 'schéma directeur énergie',
        'schéma directeur climat', 'contrat de transition écologique',
        'plan communal', 'plan intercommunal', 'plan régional climat',
        # === Énergie ===
        'transition énergétique', 'planification énergétique',
        'stratégie énergétique', 'efficacité énergétique',
        'réseau de chaleur', 'chaleur renouvelable', 'plan chaleur',
        'schéma directeur des réseaux de chaleur', 'géothermie',
        'mix énergétique', 'sobriété énergétique', 'maîtrise énergie',
        'plan énergie', 'programme énergie', 'autonomie énergétique',
        # === Émissions ===
        'décarbonation', 'décarbonisation', 'réduction des émissions',
        'suivi des émissions', 'comptabilité carbone', 'empreinte carbone',
        'gaz à effet de serre', 'budget carbone', 'bilan carbone territorial',
        'inventaire des émissions', 'diagnostic carbone',
        'scope 1 2 3', 'bilan scope', 'émissions directes indirectes',
        # === Monitoring & outils ===
        'monitoring climatique', 'tableau de bord climat',
        'plateforme climat', 'outil climat', 'logiciel climat',
        'indicateurs climat', 'suivi climatique',
        'observatoire climat', 'observatoire énergie climat',
        'outil de pilotage', 'outil de suivi', 'plateforme données',
        'logiciel bilan carbone', 'outil GES',
        # === Développement durable ===
        'reporting développement durable', 'rapport RSE',
        'stratégie développement durable', 'agenda 21',
        'plan développement durable', 'bilan développement durable',
        'rapport extra-financier', 'performance environnementale',
        # === Adaptation ===
        'adaptation climatique', 'résilience climatique',
        'vulnérabilité climatique', 'risque climatique',
        'plan adaptation', 'stratégie adaptation',
        'îlot de chaleur', 'canicule', 'inondation',
        'infrastructure verte', 'solution fondée sur la nature',
        'ville résiliente', 'résilience urbaine',
        # === Mobilité ===
        'mobilité durable', 'plan mobilité durable',
        'décarbonation transport', 'mobilité bas carbone',
        'plan déplacements', 'véhicules zéro émission',
        'mobilité active', 'transport collectif',
        # === Bâtiments & secteurs ===
        'rénovation énergétique', 'performance énergétique',
        'audit énergétique', 'bâtiment bas carbone',
        'décarbonation bâtiment', 'économie circulaire',
        'gestion des déchets', 'zéro déchet',
        # === Consulting ===
        'accompagnement climat', 'conseil climat', 'expertise climat',
        'assistance maîtrise ouvrage climat', 'AMO climat',
        'formation climat', 'sensibilisation climat',
        'bureau études climat', 'prestation climat',
        # === Finance ===
        'finance verte', 'obligations vertes', 'financement climat',
        'investissement durable', 'fonds vert', 'budget vert',
        # === Standards & cadres ===
        'convention des maires', 'SECAP', 'pacte vert européen',
        'EU taxonomie', 'CSRD', 'DPEF', 'bilan réglementaire',
        # === Vergabe-Framing ===
        'prestation de service', 'marché public', 'appel offres',
        'consultation', 'cahier des charges', 'étude climat',
        'mission conseil', 'marché études',
        # === Agriculture / land use ===
        'agriculture durable', 'usage des sols', 'séquestration carbone',
        'puits de carbone', 'agroécologie',
        # === Urbanisme & quartier ===
        'quartier durable', 'écoquartier', 'aménagement durable',
        'urbanisme climatique', 'ville durable', 'plan local urbanisme',
        # === Numérique / outils additionnels ===
        'SIG climat', 'système information climat',
        'données environnementales', 'outil pilotage énergie',
        'plateforme territoriale', 'calculateur carbone',
        # === ADEME / programmes ===
        'ADEME', 'programme ACTEE', 'contrat objectif territorial',
    ],

    'nl': [
        # === Klimaat & strategie ===
        'klimaatactieplan', 'klimaatstrategie', 'klimaatbeleid',
        'klimaattransitie', 'klimaatneutraal', 'klimaatplan',
        'klimaatakkoord', 'klimaatagenda', 'klimaatvisie',
        'gemeentelijk klimaatplan', 'lokaal klimaatbeleid',
        'klimaatdoelstellingen', 'klimaatprogramma',
        'klimaatuitvoeringsplan', 'klimaatkader', 'klimaatambitie',
        'gemeentelijk klimaatbeleid', 'regionaal klimaatplan',
        # === Emissies ===
        'broeikasgasinventaris', 'CO2-boekhouding', 'CO2-reductie',
        'CO2-neutraal', 'emissie-inventaris', 'emissiereductie',
        'CO2-uitstoot', 'koolstofboekhouding', 'klimaatvoetafdruk',
        'nul-emissie', 'emissieregistratie', 'CO2-budget',
        'CO2-monitoring', 'broeikasgasrapportage',
        'scope 1 2 3 uitstoot', 'emissiedata', 'emissiedatabase',
        # === Energie ===
        'energietransitie', 'energieplan', 'energiestrategie',
        'energiemanagement', 'regionale energiestrategie', 'RES',
        'aardgasvrij', 'van het gas af', 'energieneutraal',
        'duurzame energie', 'energieakkoord', 'lokaal energieplan',
        'energievisie', 'energietransitieplan', 'energieagenda',
        'energiemasterplan', 'energiebesparingsstrategie',
        # === Warmte ===
        'warmtevisie', 'warmtetransitie', 'warmtenet',
        'transitievisie warmte', 'warmteplan', 'warmtestrategie',
        'aardgasvrije wijken', 'warmtetransitieplan',
        'warmtebron', 'collectieve warmte', 'warmterotonde',
        'warmtenetwerk', 'restwarmte', 'geothermie',
        # === Duurzaamheid ===
        'duurzaamheidsrapportage', 'duurzaamheidsstrategie',
        'duurzaamheidsagenda', 'duurzaamheidsplan',
        'duurzaamheidsbeleid', 'duurzaamheidsambitie',
        'duurzaamheidsmonitoring', 'duurzaamheidsprogramma',
        'ESG-rapportage', 'milieubeleid', 'milieumanagement',
        'milieustrategie', 'milieurapportage',
        # === Monitoring & data ===
        'klimaatmonitor', 'CO2-monitor', 'klimaatdashboard',
        'klimaatdata', 'duurzaamheidsdashboard',
        'monitoring klimaatbeleid', 'voortgangsrapportage',
        'klimaatinformatiesysteem', 'emissieregistratiesysteem',
        # === Adaptatie ===
        'klimaatadaptatie', 'klimaatbestendig', 'klimaatrisico',
        'hittestress', 'wateroverlast', 'klimaatbestendige stad',
        'klimaatadaptatieplan', 'veerkrachtige stad',
        'groene infrastructuur', 'natuur-inclusief',
        # === Mobiliteit ===
        'duurzame mobiliteit', 'mobiliteitsplan', 'emissievrij vervoer',
        'fietsplan', 'zero-emissie zone', 'schone mobiliteit',
        # === Gebouwen & sectoren ===
        'verduurzaming gebouwen', 'isolatieprogramma',
        'circulaire economie', 'afvalstrategie', 'grondstoffenstrategie',
        # === Consultancy ===
        'klimaatadvies', 'duurzaamheidsadvies', 'energieadvies',
        'klimaatconsultancy', 'milieuadvies',
        # === Financiering ===
        'groene financiering', 'klimaatfinanciering', 'duurzaam investeren',
        'klimaatbudget', 'groene obligaties',
        # === Kaders & programma's ===
        'covenant van burgemeesters', 'Global Covenant', 'EU Green Deal',
        'nationaal klimaatplan', 'Klimaatwet',
        # === Aanbesteding ===
        'aanbesteding', 'opdracht', 'raamovereenkomst',
        'adviesopdracht', 'dienstverlening',
        # === Stedenbouw & wijk ===
        'wijkaanpak', 'duurzame wijk', 'gebiedsvisie',
        'stedelijke verduurzaming', 'omgevingsvisie',
        # === Digitaal aanvullend ===
        'GIS klimaatdata', 'informatiesysteem klimaat',
        'dataplatform energie', 'digitale monitor',
        # === Programma's aanvullend ===
        'Deltaprogramma', 'Regionale Energiestrategie',
    ],

    'sv': [
        # === Klimat & strategi ===
        'klimathandlingsplan', 'klimatstrategi', 'klimatplan',
        'klimatprogram', 'klimatmål', 'klimatneutral',
        'klimatomställning', 'klimatbudget', 'klimatanpassning',
        'kommunalt klimatarbete', 'klimatpolitik', 'klimatvision',
        'klimatramverk', 'klimatåtgärdsplan', 'klimatlöften',
        'fossilfritt', 'fossilfri kommun', 'klimatfärdplan',
        'kommunal klimatstrategi', 'regional klimatstrategi',
        'klimatavtal', 'klimatpolitiskt ramverk',
        # === Utsläpp ===
        'växthusgasinventering', 'utsläppsredovisning', 'utsläppsminskning',
        'koldioxidbudget', 'klimatbokslut', 'utsläppsberäkning',
        'växthusgasrapportering', 'koldioxidneutral', 'nollutsläpp',
        'utsläppsdata', 'utsläppsövervakning', 'klimatgasredovisning',
        'scope 1 2 3 utsläpp', 'utsläppsinventering',
        # === Energi ===
        'energiomställning', 'energiplan', 'energistrategi',
        'energieffektivisering', 'förnybar energi', 'fjärrvärme',
        'värmestrategi', 'värmeplan', 'energisystem',
        'kommunal energiplanering', 'lokal energiplan',
        'energiöversikt', 'energibalans', 'energimasterplan',
        'solenergi', 'vindkraft', 'geotermisk energi',
        # === Hållbarhet ===
        'hållbarhetsrapport', 'hållbarhetsstrategi',
        'hållbarhetsredovisning', 'hållbarhetsplan',
        'hållbarhetsprogram', 'hållbarhetsarbete',
        'miljörapportering', 'miljöstrategi', 'miljöledning',
        'miljöprogram', 'hållbarhetsmål', 'miljömål',
        # === Digital / verktyg ===
        'klimatdata', 'klimatverktyg', 'klimatplattform',
        'klimatövervakning', 'klimatdashboard', 'hållbarhetsdata',
        'klimatinformationssystem', 'utsläppsdatabas',
        # === Anpassning ===
        'klimatanpassningsplan', 'klimatanpassningsstrategi',
        'klimatrisker', 'värmebölja', 'översvämningsrisk',
        'grön infrastruktur', 'naturbaserade lösningar',
        'resilient stad', 'klimatsäkring',
        # === Mobilitet ===
        'hållbar mobilitet', 'fossilfria transporter',
        'cykelstrategi', 'kollektivtrafik', 'elfordon',
        # === Byggnader & sektorer ===
        'energirenovering', 'byggnaders energianvändning',
        'cirkulär ekonomi', 'avfallsstrategi',
        # === Finansiering ===
        'grön finansiering', 'klimatfinansiering', 'gröna obligationer',
        'klimatinvestering',
        # === Program ===
        'borgmästaravtalet', 'EU Green Deal',
        'Fossilfritt Sverige', 'klimatkontrakt',
        # === Stadsplanering ===
        'hållbar stadsutveckling', 'kvarterslösning',
        'omställningsplan', 'klimatsmart stad', 'energiomställningsplan',
    ],

    'no': [
        # === Klima & strategi ===
        'klimahandlingsplan', 'klimastrategi', 'klimaplan',
        'klimaprogram', 'klimamål', 'klimanøytral',
        'klimaomstilling', 'klimabudsjett', 'klimatilpasning',
        'kommunalt klimaarbeid', 'klimapolitikk', 'klimavisjon',
        'klimarammeverk', 'klimatiltaksplan', 'fossilfri',
        'fossilfri kommune', 'nullutslipp', 'klimafotavtrykk',
        'kommunal klimastrategi', 'regional klimastrategi',
        'klimaveiledning', 'klimakutt',
        # === Utslipp ===
        'klimaregnskap', 'utslippsregnskap', 'utslippsreduksjon',
        'karbonbudsjett', 'klimagassregnskap', 'utslippsberegning',
        'klimagassrapportering', 'karbonnøytral', 'utslippsdata',
        'utslippsovervåking', 'bærekraftsrapportering',
        'scope 1 2 3 utslipp', 'utslippsinventar',
        # === Energi ===
        'energiomstilling', 'energiplan', 'energistrategi',
        'energieffektivisering', 'fornybar energi', 'fjernvarme',
        'varmeplan', 'varmestrategi', 'energisystem',
        'kommunal energiplan', 'lokal energiplan',
        'energioversikt', 'energibalanse', 'energimasterplan',
        'solenergi', 'vindkraft', 'geotermisk energi',
        # === Bærekraft ===
        'bærekraftsrapport', 'bærekraftsstrategi',
        'bærekraftsrapportering', 'bærekraftsplan',
        'miljørapportering', 'miljøstrategi', 'miljøledelse',
        'miljøprogram', 'bærekraftsmål', 'miljømål',
        # === Digital / verktøy ===
        'klimadata', 'klimaverktøy', 'klimaplattform',
        'klimaovervåking', 'klimadashboard', 'bærekraftsdata',
        'klimainformasjonssystem', 'utslippsdatabase',
        # === Tilpasning ===
        'klimatilpasningsplan', 'klimatilpasningsstrategi',
        'klimarisiko', 'hetebølge', 'flomrisiko',
        'grønn infrastruktur', 'naturbaserte løsninger',
        'robust by', 'klimasikring',
        # === Mobilitet ===
        'bærekraftig mobilitet', 'nullutslippstransport',
        'sykkelstrategi', 'kollektivtransport', 'elbil',
        # === Bygninger & sektorer ===
        'energioppgradering', 'bygningers energibruk',
        'sirkulær økonomi', 'avfallsstrategi',
        # === Finansiering ===
        'grønn finansiering', 'klimafinansiering', 'grønne obligasjoner',
        'klimainvestering', 'Enova',
        # === Program ===
        'ordføreravtalen', 'EU Green Deal', 'Klimasats',
        'Paris-avtalen', 'klimaforlik',
        # === Byplanlegging ===
        'bærekraftig byutvikling', 'klimasmart by', 'omstillingsplan',
    ],

    'fi': [
        # === Ilmasto & strategia ===
        'ilmastosuunnitelma', 'ilmastostrategia', 'ilmasto-ohjelma',
        'ilmastotavoite', 'hiilineutraali', 'hiilineutraalius',
        'ilmastonmuutos', 'ilmastopolitiikka', 'ilmastovisio',
        'kuntien ilmastotyö', 'ilmastotoimenpideohjelma',
        'päästövähennys', 'fossiiliton', 'hiilivapaa',
        'kunnallinen ilmastostrategia', 'alueellinen ilmastostrategia',
        'ilmastotiekartta', 'ilmastokartta',
        # === Päästöt ===
        'kasvihuonekaasupäästöt', 'päästöinventaario', 'päästölaskenta',
        'hiilijalanjälki', 'päästöraportointi', 'päästöseuranta',
        'kasvihuonekaasuinventaario', 'hiilibudjetti', 'nollapäästö',
        'päästödata', 'päästövähennyspolku', 'scope 1 2 3 päästöt',
        'päästötietokanta', 'päästökirjanpito',
        # === Energia ===
        'energiasuunnitelma', 'energiastrategia', 'energiatehokkuus',
        'uusiutuva energia', 'kaukolämpö', 'lämpösuunnitelma',
        'energiajärjestelmä', 'kunnallinen energiasuunnitelma',
        'energiamurros', 'energiasiirtymä', 'energiamasterplan',
        'aurinkoenergia', 'tuulivoima', 'maalämpö',
        # === Kestävyys ===
        'kestävyysraportointi', 'kestävyysstrategia',
        'ympäristöraportointi', 'ympäristöstrategia', 'ympäristöjohtaminen',
        'ympäristöohjelma', 'kestävyystavoitteet', 'ympäristötavoitteet',
        # === Digitaalinen ===
        'ilmastodata', 'ilmastotyökalu', 'ilmastoseuranta',
        'kestävyysdata', 'ilmastodashboard', 'ilmastotietojärjestelmä',
        'päästötietojärjestelmä',
        # === Sopeutuminen ===
        'ilmastosopeutuminen', 'ilmastosopeutumissuunnitelma',
        'ilmastoriskit', 'helleaalto', 'tulvariski',
        'vihreä infrastruktuuri', 'luontopohjaiset ratkaisut',
        # === Liikenne ===
        'kestävä liikkuminen', 'päästötön liikenne',
        'pyöräilystrategia', 'joukkoliikenne', 'sähköauto',
        # === Rakennukset & sektorit ===
        'energiaremontti', 'rakennusten energiankäyttö',
        'kiertotalous', 'jätestrategia',
        # === Rahoitus ===
        'vihreä rahoitus', 'ilmastorahoitus', 'vihreät joukkovelkakirjat',
        'ilmastoinvestointi',
        # === Ohjelmat ===
        'kaupunginjohtajien sopimus', 'EU Green Deal',
        'HINKU-kunnat', 'hiilineutraali kunta',
    ],

    'da': [
        # === Klima & strategi ===
        'klimahandlingsplan', 'klimastrategi', 'klimaplan',
        'klimaprogram', 'klimamål', 'klimaneutral',
        'klimaomstilling', 'klimabudget', 'klimatilpasning',
        'kommunalt klimaarbejde', 'klimapolitik', 'klimavision',
        'klimarammeværk', 'klimaindsatsplan', 'fossilfri',
        'fossilfri kommune', 'nuludledning', 'klimaaftryk',
        'kommunal klimastrategi', 'regional klimastrategi',
        'klimafærdplan', 'klimapartnerskab', 'DK2020',
        # === Udledning ===
        'drivhusgasopgørelse', 'udledningsregnskab', 'udledningsreduktion',
        'CO2-regnskab', 'klimagasregnskab', 'udledningsberegning',
        'drivhusgasrapportering', 'CO2-neutral', 'udledningsdata',
        'bæredygtighedsrapportering', 'kulstofbudget',
        'scope 1 2 3 udledning', 'udledningsinventar',
        # === Energi ===
        'energiomstilling', 'energiplan', 'energistrategi',
        'energieffektivisering', 'vedvarende energi', 'fjernvarme',
        'varmeplan', 'varmestrategi', 'energisystem',
        'kommunal energiplan', 'lokal energiplan',
        'energioversigt', 'energibalance', 'energimasterplan',
        'solenergi', 'vindenergi', 'geotermi',
        # === Bæredygtighed ===
        'bæredygtighedsrapport', 'bæredygtighedsstrategi',
        'bæredygtighedsplan', 'miljørapportering',
        'miljøstrategi', 'miljøledelse',
        'miljøprogram', 'bæredygtighedsmål', 'miljømål',
        # === Digital / værktøj ===
        'klimadata', 'klimaværktøj', 'klimaplatform',
        'klimaovervågning', 'klimadashboard', 'bæredygtighedsdata',
        'klimainformationssystem', 'udledningsdatabase',
        # === Tilpasning ===
        'klimatilpasningsplan', 'klimatilpasningsstrategi',
        'klimarisiko', 'hedebølge', 'oversvømmelsesrisiko',
        'grøn infrastruktur', 'naturbaserede løsninger',
        'robust by', 'klimasikring',
        # === Mobilitet ===
        'bæredygtig mobilitet', 'nuludledningstransport',
        'cykelstrategi', 'kollektiv transport', 'elbil',
        # === Bygninger & sektorer ===
        'energirenovering', 'bygningers energiforbrug',
        'cirkulær økonomi', 'affaldsstrategi',
        # === Finansiering ===
        'grøn finansiering', 'klimafinansiering', 'grønne obligationer',
        'klimainvestering',
        # === Programmer ===
        'borgmesterpagten', 'EU Green Deal',
        'DK2020', 'Parisaftalen', 'klimahandlingskommune',
        'grøn omstilling',
        # === Byudvikling ===
        'bæredygtig byudvikling',
    ],
}

CPV_CODES = [
    '71313000',  # Environmental engineering consultancy
    '72000000',  # IT services
    '90700000',  # Environmental services
    '90730000',  # Pollution tracking/monitoring
    '72212000',  # Application software programming
    '72260000',  # Software-related services
    '90710000',  # Environmental management
    # New – heat planning, energy planning, climate consulting
    '71314000',  # Energy and related services
    '71314200',  # Energy management services
    '09300000',  # Electricity, heating, solar and nuclear energy
    '71240000',  # Architectural, engineering and planning services
    '73220000',  # Development consultancy services
    '48600000',  # Database and operating software package
    '79411000',  # General management consultancy services
]

HEADERS = {
    'User-Agent': 'ClimateView-RFP-Scanner/1.1 (Climate Action Procurement Intelligence)',
    'Accept': 'application/json',
}

COUNTRY_TO_MARKET = {
    'US': 'North America', 'CA': 'North America',
    'GB': 'UK + Ireland', 'IE': 'UK + Ireland',
    'DE': 'DACH', 'AT': 'DACH', 'CH': 'DACH',
    'BE': 'Benelux', 'NL': 'Benelux', 'LU': 'Benelux',
    'SE': 'Nordics', 'DK': 'Nordics', 'NO': 'Nordics', 'FI': 'Nordics',
    'FR': 'Adjacent', 'ES': 'Adjacent', 'IT': 'Adjacent', 'PT': 'Adjacent',
    'AU': 'Adjacent', 'NZ': 'Adjacent',
    'INT': 'International',
}


def generate_id(title: str, entity: str) -> str:
    """Deterministic ID from normalized title+entity (portal-independent for cross-dedup)."""
    raw = f"{title.strip().lower()}|{entity.strip().lower()}"
    return f"rfp-{hashlib.md5(raw.encode()).hexdigest()[:12]}"


def atomic_save(data: list, path: str):
    """Write to temp file then rename for crash safety."""
    dir_name = os.path.dirname(path) or '.'
    fd, tmp_path = tempfile.mkstemp(suffix='.json', dir=dir_name)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        shutil.move(tmp_path, path)
        log.info(f"Saved {len(data)} RFPs to {path}")
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def load_existing_data() -> list:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return []


def auto_expire(data: list) -> int:
    """Set status='Passed' for expired RFPs that are still 'New'. Returns count changed."""
    today = datetime.now().strftime('%Y-%m-%d')
    count = 0
    for r in data:
        if r.get('deadline') and r['deadline'] < today and r.get('status') == 'New':
            r['status'] = 'Passed'
            r['pass_reason'] = 'Deadline elapsed without action'
            count += 1
    return count


def log_scan(portal: str, rfps_found: int, new_rfps: int, updated: int = 0, error: str = None):
    logs = []
    if os.path.exists(SCAN_LOG_FILE):
        with open(SCAN_LOG_FILE) as f:
            logs = json.load(f)
    logs.append({
        'timestamp': datetime.now().isoformat(),
        'portal': portal,
        'rfps_found': rfps_found,
        'new_rfps': new_rfps,
        'updated_rfps': updated,
        'error': error
    })
    logs = logs[-500:]
    with open(SCAN_LOG_FILE, 'w') as f:
        json.dump(logs, f, indent=2)


def fetch_with_retry(session, url, params=None, timeout=30, retries=1):
    """Fetch URL with retry on failure."""
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            return resp
        except (requests.Timeout, requests.ConnectionError) as e:
            if attempt < retries:
                wait = 5 * (attempt + 1)
                log.warning(f"Retry {attempt+1}/{retries} after {wait}s for {url}: {e}")
                time.sleep(wait)
            else:
                raise


def detect_procurement_process(title, description):
    """Detect procurement process type from title/description text."""
    text = f"{title} {description}".lower()
    process = {'type': 'Standard', 'rounds': 'Single submission', 'details': []}

    # Process type detection
    if any(k in text for k in ['negotiated procedure', 'verhandlungsverfahren', 'procédure négociée', 'negotiated']):
        process['type'] = 'Negotiated Procedure'
        process['rounds'] = 'Multiple rounds likely'
        process['details'].append('Negotiation phase expected after initial submission')
    elif any(k in text for k in ['competitive dialogue', 'wettbewerblicher dialog', 'dialogue compétitif']):
        process['type'] = 'Competitive Dialogue'
        process['rounds'] = 'Multiple rounds'
        process['details'].append('Structured dialogue rounds before final tender')
    elif any(k in text for k in ['restricted procedure', 'nichtoffenes verfahren', 'procédure restreinte', 'restricted']):
        process['type'] = 'Restricted Procedure'
        process['rounds'] = 'Two stages'
        process['details'].append('Stage 1: Pre-qualification; Stage 2: Invited tender')
    elif any(k in text for k in ['open procedure', 'offenes verfahren', 'procédure ouverte']):
        process['type'] = 'Open Procedure'
        process['rounds'] = 'Single submission'
    elif any(k in text for k in ['framework agreement', 'rahmenvereinbarung', 'rahmenvertrag', 'accord-cadre']):
        process['type'] = 'Framework Agreement'
        process['rounds'] = 'Multiple call-offs'
        process['details'].append('Framework with potential mini-competitions')

    # Multi-stage detection
    if any(k in text for k in ['two-stage', 'zweistufig', 'two phase', 'zwei phasen', 'multi-stage', 'mehrstufig']):
        process['rounds'] = 'Multi-stage'
        process['details'].append('Multiple evaluation stages')
    if any(k in text for k in ['shortlist', 'pre-qualification', 'präqualifikation', 'prequalification', 'eignungsprüfung']):
        if 'Pre-qualification' not in ' '.join(process['details']):
            process['details'].append('Pre-qualification or shortlisting step')
    if any(k in text for k in ['presentation', 'präsentation', 'demo', 'demonstration', 'pitch']):
        process['details'].append('Presentation or demo may be required')
    if any(k in text for k in ['best price', 'preis-leistung', 'zuschlagskriterien', 'award criteria']):
        process['details'].append('Evaluated on price-quality criteria')

    return process


def result_to_record(result, title, entity, country, description, budget_eur, deadline, portal, url, date_found=None):
    """Convert ScoringResult to a dashboard record dict."""
    market = COUNTRY_TO_MARKET.get(country.upper(), 'Adjacent') if country else 'Unknown'
    procurement_process = detect_procurement_process(title, description or '')
    return {
        'id': generate_id(title, entity),
        'rfp_title': title,
        'issuing_entity': entity,
        'country': country,
        'market': market,
        'description': description[:2000] if description else '',
        'budget_eur': budget_eur,
        'deadline': deadline,
        'relevance_score': result.relevance_score,
        'win_probability': result.win_probability,
        'feature_alignment_score': result.feature_alignment_score,
        'geographic_fit_score': result.geographic_fit_score,
        'budget_fit_score': result.budget_fit_score,
        'timeline_score': result.timeline_score,
        'competitive_score': result.competitive_score,
        'strategic_value_score': result.strategic_value_score,
        'advisory_bonus': result.advisory_bonus,
        'feature_breakdown': result.feature_breakdown,
        'competitor_signals': result.competitor_signals,
        'competitor_recommendation': result.competitor_recommendation,
        'positive_signals': result.positive_signals,
        'edge_case_flags': result.edge_case_flags,
        'score_confidence': result.score_confidence,
        'rfp_type': result.rfp_type,
        'deadline_status': result.deadline_status,
        'scoring_config_version': result.scoring_config_version,
        'source_portal': portal,
        'source_url': url,
        'procurement_process': procurement_process,
        'date_found': date_found or datetime.now().strftime('%Y-%m-%d'),
        'scored_at': datetime.now().isoformat(),
        'last_updated': datetime.now().isoformat(),
        'added_date': datetime.now().strftime('%Y-%m-%d'),
        'status': 'New',
        'notes': '',
        'status_history': [{'status': 'New', 'date': datetime.now().isoformat(), 'by': 'scanner'}],
        'pass_reason': ''
    }


class PortalScanner:
    def __init__(self, scorer: RFPScorer):
        self.scorer = scorer
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def scan(self, lookback_days: int = 90) -> list:
        raise NotImplementedError


class SAMGovScanner(PortalScanner):
    PORTAL_NAME = 'SAM.gov'
    API_BASE = 'https://api.sam.gov/opportunities/v2/search'

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        api_key = os.environ.get('SAM_API_KEY', '')
        if not api_key:
            log.warning("SAM_API_KEY not set. Get free key at https://api.sam.gov")
            return []

        posted_from = (datetime.now() - timedelta(days=lookback_days)).strftime('%m/%d/%Y')
        posted_to = datetime.now().strftime('%m/%d/%Y')

        for keyword in KEYWORDS['en'][:55]:
            try:
                params = {
                    'api_key': api_key,
                    'postedFrom': posted_from,
                    'postedTo': posted_to,
                    'keyword': keyword,
                    'ptype': 'p,k',
                    'limit': 25,
                    'offset': 0
                }
                resp = fetch_with_retry(self.session, self.API_BASE, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    for opp in data.get('opportunitiesData', []):
                        rec = self._parse(opp)
                        if rec:
                            results.append(rec)
                elif resp.status_code == 429:
                    log.warning(f"SAM.gov rate limited on '{keyword}', waiting 60s")
                    time.sleep(60)
                time.sleep(2)
            except Exception as e:
                log.error(f"SAM.gov error for '{keyword}': {e}")
        return results

    def _parse(self, opp: dict) -> dict:
        title = opp.get('title', '')
        entity = opp.get('organizationName', '') or opp.get('departmentName', '')
        description = opp.get('description', '') or opp.get('synopsis', '') or ''
        deadline = opp.get('responseDeadLine', '')
        notice_id = opp.get('noticeId', '')

        if deadline:
            try:
                dl = datetime.strptime(deadline[:10], '%Y-%m-%d')
                if dl < datetime.now():
                    return None
                deadline = dl.strftime('%Y-%m-%d')
            except ValueError:
                deadline = None

        rfp_input = RFPInput(title=title, issuing_entity=entity, description=description[:2000],
                             country='US', deadline=deadline,
                             source_portal=self.PORTAL_NAME,
                             source_url=f"https://sam.gov/opp/{notice_id}" if notice_id else None)
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, title, entity, 'US', description, None, deadline,
                                self.PORTAL_NAME, f"https://sam.gov/opp/{notice_id}" if notice_id else None)


class TEDScanner(PortalScanner):
    PORTAL_NAME = 'TED (EU)'
    # New consolidated API domain (old ted.europa.eu/api/v3.0 is deprecated)
    API_URLS = [
        'https://api.ted.europa.eu/v3/notices/search',
        'https://ted.europa.eu/api/v3.0/notices/search',  # legacy fallback
    ]

    def _find_api_url(self):
        """Probe API URLs and return the first working one."""
        for url in self.API_URLS:
            try:
                resp = self.session.get(url, params={'query': 'cpv=72000000', 'pageSize': 1, 'pageNum': 1}, timeout=15)
                if resp.status_code in (200, 400):  # 400 = recognized but bad query, still means URL works
                    log.info(f"TED API: using {url}")
                    return url
            except Exception:
                continue
        return self.API_URLS[0]  # default to new URL

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        date_from = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y%m%d')
        api_url = self._find_api_url()

        # CPV code search
        for cpv in CPV_CODES[:4]:
            try:
                for page in range(1, 4):
                    params = {
                        'query': f'cpv={cpv} AND PD>=[{date_from}]',
                        'fields': 'ND,TI,CY,CA,DT,TVL',
                        'pageSize': 50,
                        'pageNum': page,
                    }
                    resp = fetch_with_retry(self.session, api_url, params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        notices = data.get('results', data.get('notices', []))
                        if isinstance(data, list):
                            notices = data
                        for notice in notices:
                            rec = self._parse_notice(notice)
                            if rec:
                                results.append(rec)
                        if len(notices) < 50:
                            break
                    else:
                        log.warning(f"TED CPV {cpv} page {page}: HTTP {resp.status_code}")
                        break
                    time.sleep(2)
            except Exception as e:
                log.error(f"TED error for CPV {cpv}: {e}")

        # Keyword search in multiple languages
        lang_groups = [KEYWORDS['en'][:25], KEYWORDS['de'][:25], KEYWORDS['fr'][:18],
                       KEYWORDS['nl'][:14], KEYWORDS['sv'][:10], KEYWORDS['no'][:10],
                       KEYWORDS['fi'][:10], KEYWORDS['da'][:10]]
        for lang_kws in lang_groups:
            for kw in lang_kws:
                try:
                    params = {'query': f'FT="{kw}" AND PD>=[{date_from}]',
                              'fields': 'ND,TI,CY,CA,DT,TVL',
                              'pageSize': 25, 'pageNum': 1}
                    resp = fetch_with_retry(self.session, api_url, params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        notices = data.get('results', data.get('notices', []))
                        if isinstance(data, list):
                            notices = data
                        for notice in notices:
                            rec = self._parse_notice(notice)
                            if rec:
                                results.append(rec)
                    time.sleep(2)
                except Exception as e:
                    log.error(f"TED keyword error '{kw}': {e}")
        log.info(f"TED: {len(results)} qualified notices found")
        return results

    def _parse_notice(self, notice: dict) -> dict:
        # Handle both legacy TED schema and eForms response formats
        title = notice.get('TI', notice.get('title', {}))
        if isinstance(title, dict):
            title = title.get('EN', '') or title.get('en', '') or next(iter(title.values()), '')
        entity = notice.get('CA', notice.get('MA', notice.get('buyerName', ''))) or 'Unknown'
        if isinstance(entity, dict):
            entity = entity.get('EN', '') or entity.get('officialName', '') or next(iter(entity.values()), '')
        country = (notice.get('CY', notice.get('country', '')) or '')[:2].upper() or 'EU'
        notice_id = notice.get('ND', notice.get('noticeId', notice.get('id', '')))
        deadline = notice.get('DT', notice.get('deadline', ''))
        budget = None
        tvl = notice.get('TVL', notice.get('estimatedValue', notice.get('totalValue', '')))
        if tvl:
            try:
                budget = float(str(tvl).replace(',', ''))
            except (ValueError, TypeError):
                pass

        # Get description from CONTENT field or title as fallback
        description = notice.get('CONTENT', notice.get('description', str(title)))
        if isinstance(description, dict):
            description = description.get('EN', '') or next(iter(description.values()), '')

        if deadline:
            try:
                dl_str = str(deadline)[:8]
                dl = datetime.strptime(dl_str, '%Y%m%d')
                if dl < datetime.now():
                    return None
                deadline = dl.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                try:
                    dl = datetime.strptime(str(deadline)[:10], '%Y-%m-%d')
                    if dl < datetime.now():
                        return None
                    deadline = dl.strftime('%Y-%m-%d')
                except (ValueError, TypeError):
                    deadline = None

        url = f"https://ted.europa.eu/en/notice/{notice_id}" if notice_id else None
        rfp_input = RFPInput(title=str(title), issuing_entity=str(entity),
                             description=str(description)[:2000],
                             country=country, budget_eur=budget, deadline=deadline,
                             source_portal=self.PORTAL_NAME, source_url=url)
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, str(title), str(entity), country,
                                str(description), budget, deadline, self.PORTAL_NAME, url)


class UKContractsScanner(PortalScanner):
    PORTAL_NAME = 'Contracts Finder (UK)'
    API_BASE = 'https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search'

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        published_from = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%dT00:00:00Z')
        for keyword in KEYWORDS['en'][:45]:
            try:
                params = {'keyword': keyword, 'publishedFrom': published_from, 'size': 50, 'stage': 'tender'}
                resp = fetch_with_retry(self.session, self.API_BASE, params=params)
                if resp.status_code == 200:
                    for release in resp.json().get('releases', []):
                        rec = self._parse_release(release)
                        if rec:
                            results.append(rec)
                time.sleep(2)
            except Exception as e:
                log.error(f"Contracts Finder error '{keyword}': {e}")
        return results

    def _parse_release(self, release: dict) -> dict:
        tender = release.get('tender', {})
        title = tender.get('title', '')
        description = tender.get('description', '')
        deadline = tender.get('tenderPeriod', {}).get('endDate', '')
        buyer = release.get('buyer', {})
        entity = buyer.get('name', 'Unknown')
        budget = None
        value = tender.get('value', {})
        if value.get('amount'):
            budget = value['amount']
            if value.get('currency', 'GBP') == 'GBP':
                budget = budget * 1.17

        if deadline:
            try:
                dl = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
                if dl.replace(tzinfo=None) < datetime.now():
                    return None
                deadline = dl.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                deadline = None

        rfp_input = RFPInput(title=title, issuing_entity=entity, description=description[:2000],
                             country='GB', budget_eur=round(budget) if budget else None,
                             deadline=deadline, source_portal=self.PORTAL_NAME,
                             source_url=release.get('id', ''))
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, title, entity, 'GB', description,
                                round(budget) if budget else None, deadline,
                                self.PORTAL_NAME, release.get('id', ''))


class ScotlandScanner(PortalScanner):
    """Public Contracts Scotland – OCDS API, no auth required."""
    PORTAL_NAME = 'Public Contracts Scotland'
    API_BASE = 'https://api.publiccontractsscotland.gov.uk/v1/Notices'

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        now = datetime.now()
        # API uses MM-YYYY format – collect all months in the lookback window
        months = set()
        for d in range(0, lookback_days + 1, 14):
            dt = now - timedelta(days=d)
            months.add(dt.strftime('%m-%Y'))
        months.add(now.strftime('%m-%Y'))  # always include current month

        for month in sorted(months):
            try:
                params = {'dateFrom': month, 'noticeType': 2, 'outputType': 0}
                resp = fetch_with_retry(self.session, self.API_BASE, params=params, timeout=60)
                if resp.status_code == 200:
                    data = resp.json()
                    releases = data.get('releases', []) if isinstance(data, dict) else data
                    log.info(f"  Scotland {month}: {len(releases)} notices")
                    for release in releases:
                        rec = self._parse_ocds(release)
                        if rec:
                            results.append(rec)
                else:
                    log.warning(f"Scotland {month}: HTTP {resp.status_code}")
                time.sleep(2)
            except Exception as e:
                log.error(f"Scotland error for {month}: {e}")
        return results

    def _parse_ocds(self, release: dict) -> dict:
        tender = release.get('tender', {})
        title = tender.get('title', '')
        description = tender.get('description', '')
        buyer = release.get('buyer', {})
        entity = buyer.get('name', 'Unknown')
        notice_id = release.get('id', '')

        deadline_raw = tender.get('tenderPeriod', {}).get('endDate', '')
        budget = None
        value = tender.get('value', {})
        if value.get('amount'):
            budget = value['amount']
            if value.get('currency', 'GBP') == 'GBP':
                budget = budget * 1.17  # GBP to EUR approx

        deadline = None
        if deadline_raw:
            try:
                dl = datetime.fromisoformat(deadline_raw.replace('Z', '+00:00'))
                if dl.replace(tzinfo=None) < datetime.now():
                    return None
                deadline = dl.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                pass

        url = f"https://www.publiccontractsscotland.gov.uk/Search/Search_Switch.aspx?ID={notice_id}"
        rfp_input = RFPInput(title=title, issuing_entity=entity, description=description[:2000],
                             country='GB', budget_eur=round(budget) if budget else None,
                             deadline=deadline, source_portal=self.PORTAL_NAME, source_url=url)
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, title, entity, 'GB', description,
                                round(budget) if budget else None, deadline, self.PORTAL_NAME, url)


class WalesScanner(PortalScanner):
    """Sell2Wales – OCDS API, same format as Scotland."""
    PORTAL_NAME = 'Sell2Wales'
    API_BASE = 'https://api.sell2wales.gov.wales/v1/Notices'

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        now = datetime.now()
        months = set()
        for d in range(0, lookback_days + 1, 14):
            dt = now - timedelta(days=d)
            months.add(dt.strftime('%m-%Y'))
        months.add(now.strftime('%m-%Y'))

        for month in sorted(months):
            try:
                params = {'dateFrom': month, 'noticeType': 2, 'outputType': 0}
                resp = fetch_with_retry(self.session, self.API_BASE, params=params, timeout=60)
                if resp.status_code == 200:
                    data = resp.json()
                    releases = data.get('releases', []) if isinstance(data, dict) else data
                    log.info(f"  Wales {month}: {len(releases)} notices")
                    for release in releases:
                        rec = self._parse_ocds(release)
                        if rec:
                            results.append(rec)
                else:
                    log.warning(f"Wales {month}: HTTP {resp.status_code}")
                time.sleep(2)
            except Exception as e:
                log.error(f"Wales error for {month}: {e}")
        return results

    def _parse_ocds(self, release: dict) -> dict:
        tender = release.get('tender', {})
        title = tender.get('title', '')
        description = tender.get('description', '')
        buyer = release.get('buyer', {})
        entity = buyer.get('name', 'Unknown')
        notice_id = release.get('id', '')

        deadline_raw = tender.get('tenderPeriod', {}).get('endDate', '')
        budget = None
        value = tender.get('value', {})
        if value.get('amount'):
            budget = value['amount']
            if value.get('currency', 'GBP') == 'GBP':
                budget = budget * 1.17

        deadline = None
        if deadline_raw:
            try:
                dl = datetime.fromisoformat(deadline_raw.replace('Z', '+00:00'))
                if dl.replace(tzinfo=None) < datetime.now():
                    return None
                deadline = dl.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                pass

        url = f"https://www.sell2wales.gov.wales/Search/Search_Switch.aspx?ID={notice_id}"
        rfp_input = RFPInput(title=title, issuing_entity=entity, description=description[:2000],
                             country='GB', budget_eur=round(budget) if budget else None,
                             deadline=deadline, source_portal=self.PORTAL_NAME, source_url=url)
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, title, entity, 'GB', description,
                                round(budget) if budget else None, deadline, self.PORTAL_NAME, url)


class DoffinScanner(PortalScanner):
    """Doffin (Norway) – Azure API Management. Requires DOFFIN_API_KEY env var."""
    PORTAL_NAME = 'Doffin (Norway)'
    API_BASE = 'https://dof-notices-prod-api.developer.azure-api.net'

    def scan(self, lookback_days: int = 90) -> list:
        api_key = os.environ.get('DOFFIN_API_KEY', '')
        if not api_key:
            log.info("DOFFIN_API_KEY not set – skipping. Get key at https://dof-notices-prod-api.developer.azure-api.net/")
            return []

        results = []
        date_from = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        headers = {'Ocp-Apim-Subscription-Key': api_key}

        for kw in KEYWORDS['no'][:25] + KEYWORDS['en'][:18]:
            try:
                params = {'keyword': kw, 'publishedFrom': date_from, 'size': 50}
                resp = self.session.get(f"{self.API_BASE}/api/v1/notices", params=params,
                                        headers=headers, timeout=30)
                if resp.status_code == 200:
                    for notice in resp.json().get('notices', resp.json() if isinstance(resp.json(), list) else []):
                        rec = self._parse(notice)
                        if rec:
                            results.append(rec)
                elif resp.status_code == 401:
                    log.warning("Doffin: Invalid API key")
                    return results
                time.sleep(2)
            except Exception as e:
                log.error(f"Doffin error '{kw}': {e}")
        return results

    def _parse(self, notice: dict) -> dict:
        title = notice.get('title', '')
        entity = notice.get('buyerName', notice.get('organization', 'Unknown'))
        description = notice.get('description', str(title))
        deadline = notice.get('deadline', notice.get('tenderDeadline', ''))
        notice_id = notice.get('id', notice.get('noticeId', ''))
        budget = notice.get('estimatedValue', None)

        if deadline:
            try:
                dl = datetime.fromisoformat(str(deadline).replace('Z', '+00:00'))
                if dl.replace(tzinfo=None) < datetime.now():
                    return None
                deadline = dl.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                deadline = None

        url = f"https://doffin.no/notices/{notice_id}" if notice_id else None
        rfp_input = RFPInput(title=title, issuing_entity=str(entity), description=str(description)[:2000],
                             country='NO', budget_eur=round(float(budget) * 0.089) if budget else None,
                             deadline=deadline, source_portal=self.PORTAL_NAME, source_url=url)
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, title, str(entity), 'NO', str(description),
                                round(float(budget) * 0.089) if budget else None,
                                deadline, self.PORTAL_NAME, url)


class HilmaScanner(PortalScanner):
    """Hilma (Finland) – Azure API Management. Requires HILMA_API_KEY env var."""
    PORTAL_NAME = 'Hilma (Finland)'
    API_BASE = 'https://hns-hilma-prod-apim.developer.azure-api.net'

    def scan(self, lookback_days: int = 90) -> list:
        api_key = os.environ.get('HILMA_API_KEY', '')
        if not api_key:
            log.info("HILMA_API_KEY not set – skipping. Get key at https://hns-hilma-prod-apim.developer.azure-api.net/")
            return []

        results = []
        headers = {'Ocp-Apim-Subscription-Key': api_key}

        for kw in KEYWORDS['fi'][:22] + KEYWORDS['en'][:18]:
            try:
                params = {'keyword': kw, 'size': 50}
                resp = self.session.get(f"{self.API_BASE}/hilmatenders", params=params,
                                        headers=headers, timeout=30)
                if resp.status_code == 200:
                    tenders = resp.json() if isinstance(resp.json(), list) else resp.json().get('tenders', [])
                    for tender in tenders:
                        rec = self._parse(tender)
                        if rec:
                            results.append(rec)
                elif resp.status_code == 401:
                    log.warning("Hilma: Invalid API key")
                    return results
                time.sleep(2)
            except Exception as e:
                log.error(f"Hilma error '{kw}': {e}")
        return results

    def _parse(self, tender: dict) -> dict:
        title = tender.get('name', tender.get('title', ''))
        entity = tender.get('organization', tender.get('buyerName', 'Unknown'))
        description = tender.get('description', str(title))
        deadline = tender.get('tenderDate', tender.get('deadline', ''))
        tender_id = tender.get('id', '')
        budget = tender.get('estimatedValue', None)

        if deadline:
            try:
                dl = datetime.fromisoformat(str(deadline).replace('Z', '+00:00'))
                if dl.replace(tzinfo=None) < datetime.now():
                    return None
                deadline = dl.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                deadline = None

        url = f"https://www.hankintailmoitukset.fi/en/notice/{tender_id}" if tender_id else None
        rfp_input = RFPInput(title=str(title), issuing_entity=str(entity), description=str(description)[:2000],
                             country='FI', budget_eur=round(float(budget)) if budget else None,
                             deadline=deadline, source_portal=self.PORTAL_NAME, source_url=url)
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, str(title), str(entity), 'FI', str(description),
                                round(float(budget)) if budget else None,
                                deadline, self.PORTAL_NAME, url)


# ── Free API-based scanners ──────────────────────────────────────────────────

class BOAMPScanner(PortalScanner):
    """BOAMP (France) – Official French procurement bulletin. Free Opendatasoft API."""
    PORTAL_NAME = 'BOAMP (France)'
    API_BASE = 'https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records'

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        date_from = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        keywords = KEYWORDS['fr'][:50] + KEYWORDS['en'][:18]

        for kw in keywords:
            try:
                params = {
                    'select': 'idweb,intitule,nomacheteur,datecloture,descripteur,nature',
                    'where': f'search(intitule,"{kw}") AND dateparution>="{date_from}"',
                    'limit': 50,
                    'order_by': 'dateparution DESC',
                }
                resp = fetch_with_retry(self.session, self.API_BASE, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    records = data.get('results', [])
                    for record in records:
                        rec = self._parse(record)
                        if rec:
                            results.append(rec)
                elif resp.status_code == 403:
                    log.warning("BOAMP: API access denied (403)")
                    return results
                time.sleep(2)
            except Exception as e:
                log.error(f"BOAMP error '{kw}': {e}")
        log.info(f"BOAMP: {len(results)} qualified notices found")
        return results

    def _parse(self, record: dict) -> dict:
        title = record.get('intitule', '')
        entity = record.get('nomacheteur', 'Unknown')
        deadline = record.get('datecloture', '')
        idweb = record.get('idweb', '')
        description = record.get('descripteur', '') or str(title)
        nature = record.get('nature', '')
        full_desc = f"{title}. {description}. {nature}".strip()

        if deadline:
            try:
                dl = datetime.strptime(str(deadline)[:10], '%Y-%m-%d')
                if dl < datetime.now():
                    return None
                deadline = dl.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                deadline = None

        url = f"https://www.boamp.fr/avis/detail/{idweb}" if idweb else None
        rfp_input = RFPInput(title=title, issuing_entity=entity, description=full_desc[:2000],
                             country='FR', deadline=deadline,
                             source_portal=self.PORTAL_NAME, source_url=url)
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, title, entity, 'FR', full_desc,
                                None, deadline, self.PORTAL_NAME, url)


class WorldBankScanner(PortalScanner):
    """World Bank Procurement Notices – Free JSON API, no auth."""
    PORTAL_NAME = 'World Bank'
    API_BASE = 'https://search.worldbank.org/api/v2/procnotices'

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        for kw in KEYWORDS['en'][:45]:
            try:
                params = {
                    'format': 'json',
                    'qterm': kw,
                    'rows': 50,
                    'os': 0,
                }
                resp = fetch_with_retry(self.session, self.API_BASE, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    notices = data.get('procnotices', {})
                    if isinstance(notices, dict):
                        for nid, notice in notices.items():
                            if isinstance(notice, dict):
                                rec = self._parse(nid, notice)
                                if rec:
                                    results.append(rec)
                time.sleep(2)
            except Exception as e:
                log.error(f"World Bank error '{kw}': {e}")
        log.info(f"World Bank: {len(results)} qualified notices found")
        return results

    def _parse(self, nid: str, notice: dict) -> dict:
        title = notice.get('project_name', notice.get('notice_lang_name', ''))
        entity = notice.get('borrower', notice.get('bid_reference_no', 'World Bank'))
        country_name = notice.get('project_ctry_name', '')
        deadline = notice.get('submission_deadline_date', '')
        description = notice.get('notice_text', notice.get('procurement_group', title))

        if deadline:
            try:
                for fmt in ['%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d', '%m/%d/%Y']:
                    try:
                        dl = datetime.strptime(str(deadline)[:19], fmt)
                        if dl < datetime.now():
                            return None
                        deadline = dl.strftime('%Y-%m-%d')
                        break
                    except ValueError:
                        continue
                else:
                    deadline = None
            except (ValueError, TypeError):
                deadline = None

        url = f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{nid}"
        rfp_input = RFPInput(title=str(title), issuing_entity=str(entity),
                             description=str(description)[:2000],
                             country='INT', deadline=deadline,
                             source_portal=self.PORTAL_NAME, source_url=url)
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, str(title), str(entity), 'INT',
                                str(description), None, deadline, self.PORTAL_NAME, url)


class TenderNedRSSScanner(PortalScanner):
    """TenderNed (Netherlands) – Public RSS feed, no auth."""
    PORTAL_NAME = 'TenderNed (Netherlands)'
    RSS_URL = 'https://www.tenderned.nl/papi/tenderned-rs-tns/rss/laatste-publicatie.rss'

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        try:
            resp = fetch_with_retry(self.session, self.RSS_URL, timeout=30)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, 'xml')
                items = soup.find_all('item')
                log.info(f"TenderNed RSS: {len(items)} items in feed")
                for item in items:
                    rec = self._parse_item(item)
                    if rec:
                        results.append(rec)
            else:
                log.warning(f"TenderNed RSS: HTTP {resp.status_code}")
        except Exception as e:
            log.error(f"TenderNed RSS error: {e}")
        log.info(f"TenderNed: {len(results)} qualified notices found")
        return results

    def _parse_item(self, item) -> dict:
        title = item.find('title').text.strip() if item.find('title') else ''
        description = item.find('description').text.strip() if item.find('description') else title
        link = item.find('link').text.strip() if item.find('link') else ''
        pub_date = item.find('pubDate').text.strip() if item.find('pubDate') else ''

        # Strip HTML from description
        if '<' in description:
            description = BeautifulSoup(description, 'lxml').get_text(separator=' ')

        rfp_input = RFPInput(title=title, issuing_entity='Netherlands',
                             description=description[:2000],
                             country='NL', source_portal=self.PORTAL_NAME, source_url=link)
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, title, 'Netherlands', 'NL', description,
                                None, None, self.PORTAL_NAME, link)


# ── Experimental web scrapers ────────────────────────────────────────────────
# These attempt to scrape search results from web-only portals.
# They may break if the portal changes its HTML structure.
# Each one fails gracefully – logs an error and returns [].

SCRAPER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5,de;q=0.3',
}


class SIMAPScanner(PortalScanner):
    """SIMAP.ch (Switzerland) – Scrape public search results."""
    PORTAL_NAME = 'SIMAP.ch (Switzerland)'
    SEARCH_URL = 'https://www.simap.ch/api/searchpublications'

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        keywords = KEYWORDS['de'][:25] + KEYWORDS['fr'][:10] + KEYWORDS['en'][:10]

        for kw in keywords:
            try:
                # Try the SIMAP REST API first (public search endpoint)
                params = {'searchText': kw, 'publicationType': 'TENDER', 'pageSize': 50, 'page': 0}
                resp = self.session.get(self.SEARCH_URL, params=params, timeout=30)
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        publications = data if isinstance(data, list) else data.get('content', data.get('publications', []))
                        for pub in publications:
                            rec = self._parse_api(pub)
                            if rec:
                                results.append(rec)
                    except ValueError:
                        # Not JSON – try HTML scraping as fallback
                        self._scrape_html(resp.text, results)
                elif resp.status_code in (401, 403, 404):
                    log.info(f"SIMAP API not accessible ({resp.status_code}), trying HTML scrape")
                    results.extend(self._scrape_fallback(kw))
                time.sleep(3)
            except Exception as e:
                log.error(f"SIMAP error '{kw}': {e}")
        log.info(f"SIMAP.ch: {len(results)} qualified notices found")
        return results

    def _parse_api(self, pub: dict) -> dict:
        title = pub.get('title', pub.get('projectTitle', ''))
        entity = pub.get('organization', pub.get('buyer', 'Unknown'))
        description = pub.get('description', str(title))
        deadline = pub.get('deadline', pub.get('submissionDeadline', ''))
        pub_id = pub.get('id', pub.get('projectId', ''))

        if deadline:
            try:
                dl = datetime.fromisoformat(str(deadline).replace('Z', '+00:00'))
                if dl.replace(tzinfo=None) < datetime.now():
                    return None
                deadline = dl.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                deadline = None

        url = f"https://www.simap.ch/en/procurement/{pub_id}" if pub_id else None
        rfp_input = RFPInput(title=str(title), issuing_entity=str(entity),
                             description=str(description)[:2000],
                             country='CH', source_portal=self.PORTAL_NAME, source_url=url)
        result = self.scorer.score(rfp_input)
        if not result.qualified:
            return None
        return result_to_record(result, str(title), str(entity), 'CH',
                                str(description), None, deadline, self.PORTAL_NAME, url)

    def _scrape_html(self, html: str, results: list):
        soup = BeautifulSoup(html, 'lxml')
        for link in soup.find_all('a', href=True):
            href = link.get('href', '')
            if '/procurement/' in href or '/project/' in href:
                title = link.get_text(strip=True)
                if title and len(title) > 10:
                    rfp_input = RFPInput(title=title, issuing_entity='Switzerland',
                                         description=title, country='CH',
                                         source_portal=self.PORTAL_NAME,
                                         source_url=f"https://www.simap.ch{href}")
                    result = self.scorer.score(rfp_input)
                    if result.qualified:
                        results.append(result_to_record(result, title, 'Switzerland', 'CH',
                                                        title, None, None, self.PORTAL_NAME,
                                                        f"https://www.simap.ch{href}"))

    def _scrape_fallback(self, keyword: str) -> list:
        """Fallback: try the public HTML search page."""
        try:
            url = f"https://www.simap.ch/en/procurement?searchText={quote_plus(keyword)}"
            resp = self.session.get(url, headers=SCRAPER_HEADERS, timeout=30)
            if resp.status_code == 200:
                results = []
                self._scrape_html(resp.text, results)
                return results
        except Exception:
            pass
        return []


class GermanFederalScanner(PortalScanner):
    """German Federal Procurement – scrape service.bund.de and evergabe-online.de."""
    PORTAL_NAME = 'Bund.de (Germany)'

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        # Try evergabe-online.de search (may have JSON endpoint)
        results.extend(self._scan_evergabe(lookback_days))
        # Try service.bund.de
        results.extend(self._scan_bund(lookback_days))
        log.info(f"German Federal: {len(results)} qualified notices found")
        return results

    def _scan_evergabe(self, lookback_days: int) -> list:
        results = []
        base = 'https://www.evergabe-online.de/tenderdetails.html'
        search_url = 'https://www.evergabe-online.de/searchresult.html'

        for kw in KEYWORDS['de'][:35] + KEYWORDS['en'][:10]:
            try:
                params = {'searchText': kw}
                resp = self.session.get(search_url, params=params, headers=SCRAPER_HEADERS, timeout=30)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.content, 'lxml')
                    for row in soup.select('table.searchResult tr, div.tenderRow, div.search-result-item'):
                        title_el = row.find('a')
                        if title_el and title_el.get_text(strip=True):
                            title = title_el.get_text(strip=True)
                            href = title_el.get('href', '')
                            full_url = f"https://www.evergabe-online.de{href}" if href.startswith('/') else href
                            rfp_input = RFPInput(title=title, issuing_entity='German Federal',
                                                 description=title, country='DE',
                                                 source_portal=self.PORTAL_NAME, source_url=full_url)
                            result = self.scorer.score(rfp_input)
                            if result.qualified:
                                results.append(result_to_record(result, title, 'German Federal', 'DE',
                                                                title, None, None, self.PORTAL_NAME, full_url))
                time.sleep(3)
            except Exception as e:
                log.error(f"evergabe-online error '{kw}': {e}")
        return results

    def _scan_bund(self, lookback_days: int) -> list:
        results = []
        search_url = 'https://www.service.bund.de/Content/DE/Ausschreibungen/suche.html'

        for kw in KEYWORDS['de'][:25]:
            try:
                params = {'searchtext': kw, 'resultsPerPage': 50}
                resp = self.session.get(search_url, params=params, headers=SCRAPER_HEADERS, timeout=30)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.content, 'lxml')
                    for link in soup.find_all('a', href=True):
                        href = link.get('href', '')
                        title = link.get_text(strip=True)
                        if ('ausschreibung' in href.lower() or 'vergabe' in href.lower()) and len(title) > 15:
                            full_url = f"https://www.service.bund.de{href}" if href.startswith('/') else href
                            rfp_input = RFPInput(title=title, issuing_entity='German Federal',
                                                 description=title, country='DE',
                                                 source_portal=self.PORTAL_NAME, source_url=full_url)
                            result = self.scorer.score(rfp_input)
                            if result.qualified:
                                results.append(result_to_record(result, title, 'German Federal', 'DE',
                                                                title, None, None, self.PORTAL_NAME, full_url))
                time.sleep(3)
            except Exception as e:
                log.error(f"service.bund.de error '{kw}': {e}")
        return results


class AustrianScanner(PortalScanner):
    """Austrian Procurement – scrape auftrag.at public search."""
    PORTAL_NAME = 'auftrag.at (Austria)'

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        search_url = 'https://www.auftrag.at/Search/FulltextSearch'

        for kw in KEYWORDS['de'][:35] + KEYWORDS['en'][:10]:
            try:
                params = {'searchTerm': kw, 'page': 1, 'pageSize': 50}
                resp = self.session.get(search_url, params=params, headers=SCRAPER_HEADERS, timeout=30)
                if resp.status_code == 200:
                    # Try JSON first (some portals return JSON)
                    try:
                        data = resp.json()
                        items = data if isinstance(data, list) else data.get('results', data.get('items', []))
                        for item in items:
                            title = item.get('title', item.get('name', ''))
                            entity = item.get('buyer', item.get('organization', 'Austria'))
                            rec_id = item.get('id', '')
                            url = f"https://www.auftrag.at/Tender/{rec_id}" if rec_id else None
                            rfp_input = RFPInput(title=title, issuing_entity=str(entity),
                                                 description=title, country='AT',
                                                 source_portal=self.PORTAL_NAME, source_url=url)
                            result = self.scorer.score(rfp_input)
                            if result.qualified:
                                results.append(result_to_record(result, title, str(entity), 'AT',
                                                                title, None, None, self.PORTAL_NAME, url))
                    except ValueError:
                        # HTML response – parse it
                        soup = BeautifulSoup(resp.content, 'lxml')
                        for link in soup.find_all('a', href=True):
                            href = link.get('href', '')
                            title = link.get_text(strip=True)
                            if '/Tender/' in href and len(title) > 10:
                                full_url = f"https://www.auftrag.at{href}" if href.startswith('/') else href
                                rfp_input = RFPInput(title=title, issuing_entity='Austria',
                                                     description=title, country='AT',
                                                     source_portal=self.PORTAL_NAME, source_url=full_url)
                                result = self.scorer.score(rfp_input)
                                if result.qualified:
                                    results.append(result_to_record(result, title, 'Austria', 'AT',
                                                                    title, None, None, self.PORTAL_NAME, full_url))
                time.sleep(3)
            except Exception as e:
                log.error(f"auftrag.at error '{kw}': {e}")
        log.info(f"auftrag.at: {len(results)} qualified notices found")
        return results


class IrishTendersScanner(PortalScanner):
    """eTenders Ireland – scrape public search results."""
    PORTAL_NAME = 'eTenders (Ireland)'

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        search_url = 'https://www.etenders.gov.ie/epps/cft/listContractNotices.do'

        for kw in KEYWORDS['en'][:35]:
            try:
                params = {'d-8588276-p': 1, 'searchTerm': kw}
                resp = self.session.get(search_url, params=params, headers=SCRAPER_HEADERS, timeout=30)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.content, 'lxml')
                    # eTenders uses tables for results
                    for row in soup.select('table tr, div.notice-row, li.result-item'):
                        link = row.find('a', href=True)
                        if link:
                            title = link.get_text(strip=True)
                            href = link.get('href', '')
                            if len(title) > 10 and ('notice' in href.lower() or 'cft' in href.lower()):
                                full_url = f"https://www.etenders.gov.ie{href}" if href.startswith('/') else href
                                rfp_input = RFPInput(title=title, issuing_entity='Ireland',
                                                     description=title, country='IE',
                                                     source_portal=self.PORTAL_NAME, source_url=full_url)
                                result = self.scorer.score(rfp_input)
                                if result.qualified:
                                    results.append(result_to_record(result, title, 'Ireland', 'IE',
                                                                    title, None, None,
                                                                    self.PORTAL_NAME, full_url))
                time.sleep(3)
            except Exception as e:
                log.error(f"eTenders error '{kw}': {e}")
        log.info(f"eTenders Ireland: {len(results)} qualified notices found")
        return results


class UNGMScanner(PortalScanner):
    """UNGM (UN Global Marketplace) – scrape public notice search."""
    PORTAL_NAME = 'UNGM'
    SEARCH_URL = 'https://www.ungm.org/Public/Notice'

    def scan(self, lookback_days: int = 90) -> list:
        results = []
        for kw in KEYWORDS['en'][:25]:
            try:
                # Try the UNGM public search page
                params = {'PageIndex': 0, 'Title': kw}
                resp = self.session.get(self.SEARCH_URL, params=params, headers=SCRAPER_HEADERS, timeout=30)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.content, 'lxml')
                    for row in soup.select('table tr, div.notice, div.row'):
                        link = row.find('a', href=True)
                        if link:
                            title = link.get_text(strip=True)
                            href = link.get('href', '')
                            if len(title) > 10 and ('Notice' in href or 'notice' in href):
                                full_url = f"https://www.ungm.org{href}" if href.startswith('/') else href
                                rfp_input = RFPInput(title=title, issuing_entity='United Nations',
                                                     description=title, country='INT',
                                                     source_portal=self.PORTAL_NAME, source_url=full_url)
                                result = self.scorer.score(rfp_input)
                                if result.qualified:
                                    results.append(result_to_record(result, title, 'United Nations', 'INT',
                                                                    title, None, None,
                                                                    self.PORTAL_NAME, full_url))
                time.sleep(3)
            except Exception as e:
                log.error(f"UNGM error '{kw}': {e}")
        log.info(f"UNGM: {len(results)} qualified notices found")
        return results


SCANNERS = {
    # Tier 1: API-based (reliable)
    'sam': SAMGovScanner,
    'ted': TEDScanner,
    'uk': UKContractsScanner,
    'scotland': ScotlandScanner,
    'wales': WalesScanner,
    'boamp': BOAMPScanner,
    'worldbank': WorldBankScanner,
    'tenderned': TenderNedRSSScanner,
    # Tier 2: API with optional key
    'doffin': DoffinScanner,
    'hilma': HilmaScanner,
    # Tier 3: Web scrapers (experimental – may break)
    'simap': SIMAPScanner,
    'germany': GermanFederalScanner,
    'austria': AustrianScanner,
    'ireland': IrishTendersScanner,
    'ungm': UNGMScanner,
}


def run_scan(portals=None, lookback_days=30, dry_run=False):
    scorer = RFPScorer(os.path.join(SCRIPT_DIR, 'rfp_scoring_config.json'))
    existing = load_existing_data()

    # Auto-expire stale records
    expired_count = auto_expire(existing)
    if expired_count:
        log.info(f"Auto-expired {expired_count} stale RFPs")

    existing_by_id = {r['id']: r for r in existing}

    if portals is None:
        portals = list(SCANNERS.keys())

    all_new = []
    all_updated = 0
    for portal_key in portals:
        if portal_key not in SCANNERS:
            log.warning(f"Unknown portal: {portal_key}")
            continue
        log.info(f"Scanning {portal_key}...")
        scanner = SCANNERS[portal_key](scorer)
        try:
            results = scanner.scan(lookback_days=lookback_days)
            # Cross-portal dedup + update logic
            new_count = 0
            updated_count = 0
            for r in results:
                rid = r['id']
                if rid in existing_by_id:
                    # Update: merge new info into existing record
                    old = existing_by_id[rid]
                    changed = False
                    for field in ['deadline', 'budget_eur', 'relevance_score', 'win_probability',
                                  'deadline_status', 'competitor_recommendation', 'description']:
                        if r.get(field) and r[field] != old.get(field):
                            old[field] = r[field]
                            changed = True
                    if changed:
                        old['last_updated'] = datetime.now().strftime('%Y-%m-%d')
                        updated_count += 1
                else:
                    existing.append(r)
                    existing_by_id[rid] = r
                    new_count += 1
                    all_new.append(r)
            all_updated += updated_count
            log.info(f"  {portal_key}: {len(results)} found, {new_count} new, {updated_count} updated")
            log_scan(portal_key, len(results), new_count, updated_count)
        except Exception as e:
            log.error(f"  {portal_key} FAILED: {e}")
            log_scan(portal_key, 0, 0, 0, str(e))

    if dry_run:
        log.info(f"\n[DRY RUN] Would add {len(all_new)} new, update {all_updated}")
        for r in all_new:
            log.info(f"  + {r['rfp_title'][:60]} | {r['relevance_score']} | {r['win_probability']}")
        return all_new

    # Remove records expired >30 days ago (keep Won/Submitted indefinitely)
    cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    existing = [r for r in existing if
                not r.get('deadline') or
                r['deadline'] >= cutoff or
                r.get('status') in ('Submitted', 'Won', 'Reviewing')]

    atomic_save(existing, DATA_FILE)
    log.info(f"Total active: {len(existing)}, new: {len(all_new)}, updated: {all_updated}")
    return all_new


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='ClimateView RFP Scanner v1.1')
    parser.add_argument('--portal', type=str, help='Specific portal (sam, ted, uk)')
    parser.add_argument('--days', type=int, default=20, help='Lookback days')
    parser.add_argument('--dry-run', action='store_true', help='Preview without saving')
    args = parser.parse_args()
    portals = [args.portal] if args.portal else None
    run_scan(portals=portals, lookback_days=args.days, dry_run=args.dry_run)
