#!/usr/bin/env python3
"""
ClimateView RFP Digest Sender

Sends daily email summaries of new/changed RFPs to maxime@climateview.global.
Uses Outlook-compatible HTML email with inline CSS and table-based layout.
"""

import json
import os
import sys
import smtplib
import logging
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def parse_iso(s):
    """Parse ISO date string, handling Z suffix for Python < 3.11."""
    if not s:
        raise ValueError("Empty date string")
    cleaned = s.replace('Z', '+00:00').replace('+00:00', '')
    return datetime.fromisoformat(cleaned)


# Constants
SCRIPT_DIR = Path(__file__).parent
RFP_DATA_FILE = SCRIPT_DIR / 'rfp_data.json'
LAST_RUN_FILE = SCRIPT_DIR / '.digest_last_run'
DEFAULT_RECIPIENT = 'maxime@climateview.global'
SCORING_CONFIG_VERSION = '1.0'

# Color scheme for dashboard link
DASHBOARD_LINK = 'https://climateview.global/dashboard/rfps'


class RFPDigest:
    """Manages RFP digest generation and sending."""

    def __init__(self):
        """Initialize the digest generator."""
        self.rfp_data = {}
        self.last_run_timestamp = None
        self.new_rfps = []
        self.deadline_alerts = []
        self.score_changes = []
        self.all_active_rfps = []
        self.high_prob_rfps = []

    def load_rfp_data(self):
        """Load RFP data from JSON file."""
        if not RFP_DATA_FILE.exists():
            logger.error(f"RFP data file not found: {RFP_DATA_FILE}")
            return False

        try:
            with open(RFP_DATA_FILE, 'r') as f:
                self.rfp_data = json.load(f)

            if not self.rfp_data:
                logger.error("RFP data file is empty")
                return False

            logger.info(f"Loaded {len(self.rfp_data)} RFPs from data file")
            return True
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse RFP data: {e}")
            return False

    def load_last_run(self):
        """Load timestamp of last digest run."""
        if LAST_RUN_FILE.exists():
            try:
                with open(LAST_RUN_FILE, 'r') as f:
                    timestamp_str = f.read().strip()
                    self.last_run_timestamp = parse_iso(timestamp_str)
                    logger.info(f"Last digest run: {self.last_run_timestamp}")
            except (ValueError, IOError) as e:
                logger.warning(f"Could not load last run timestamp: {e}")
                self.last_run_timestamp = None
        else:
            logger.info("No previous digest run found")
            self.last_run_timestamp = None

    def save_last_run(self):
        """Save current timestamp as last digest run."""
        try:
            now = datetime.now()
            with open(LAST_RUN_FILE, 'w') as f:
                f.write(now.isoformat())
            logger.info(f"Saved last run timestamp: {now}")
        except IOError as e:
            logger.error(f"Failed to save last run timestamp: {e}")

    def process_rfps(self):
        """Identify new RFPs, deadline alerts, and score changes."""
        now = datetime.now()

        for rfp in self.rfp_data:
            rfp_id = rfp.get('id', 'unknown')
            # Parse dates
            try:
                added_date = parse_iso(rfp.get('added_date', ''))
                last_updated = parse_iso(rfp.get('last_updated', ''))
                deadline_date = parse_iso(rfp.get('deadline', ''))
            except (ValueError, TypeError):
                logger.warning(f"Invalid date format in RFP {rfp_id}")
                continue

            # Only process qualified RFPs
            if rfp.get('qualified') != True:
                continue

            self.all_active_rfps.append(rfp)

            # Check for NEW RFPs
            if self.last_run_timestamp is None or added_date > self.last_run_timestamp:
                self.new_rfps.append(rfp)
                if rfp.get('win_probability', '') in ('High',):
                    self.high_prob_rfps.append(rfp)

            # Check for DEADLINE ALERTS
            days_until_deadline = (deadline_date - now).days
            deadline_status = rfp.get('deadline_status', '')

            if deadline_status == 'urgent' or (deadline_status == 'closing_soon' and days_until_deadline < 21):
                self.deadline_alerts.append(rfp)

            # Check for SCORE CHANGES
            if self.last_run_timestamp and last_updated > self.last_run_timestamp:
                if rfp not in self.new_rfps:  # Don't double-count new RFPs
                    self.score_changes.append(rfp)

        # Sort deadline alerts by deadline ascending
        self.deadline_alerts.sort(
            key=lambda x: parse_iso(x.get('deadline', '9999-12-31'))
        )

        logger.info(f"Found {len(self.new_rfps)} new RFPs")
        logger.info(f"Found {len(self.deadline_alerts)} deadline alerts")
        logger.info(f"Found {len(self.score_changes)} score changes")

    def has_updates(self):
        """Check if there are any updates to report."""
        return bool(self.new_rfps or self.deadline_alerts or self.score_changes)

    def get_summary_stats(self):
        """Generate summary statistics."""
        qualified_count = len(self.all_active_rfps)
        new_count = len(self.new_rfps)
        high_prob_count = len(self.high_prob_rfps)
        alert_count = len(self.deadline_alerts)

        return {
            'qualified': qualified_count,
            'new': new_count,
            'high_prob': high_prob_count,
            'alerts': alert_count,
        }

    def get_status_breakdown(self):
        """Get count of RFPs by status."""
        breakdown = {'New': 0, 'Reviewing': 0, 'Shortlisted': 0, 'Other': 0}

        for rfp in self.all_active_rfps:
            status = rfp.get('status', 'Other')
            if status in breakdown:
                breakdown[status] += 1
            else:
                breakdown['Other'] += 1

        return breakdown

    def format_currency(self, value):
        """Format value as currency."""
        if value is None:
            return 'N/A'
        if value >= 1e6:
            return f"${value/1e6:.1f}M"
        if value >= 1e3:
            return f"${value/1e3:.0f}K"
        return f"${value:.0f}"

    def get_score_color(self, score):
        """Get background color for score cell."""
        score = float(score or 0)
        if score >= 70:
            return '#90EE90'  # Light green
        elif score >= 40:
            return '#FFFFE0'  # Light yellow
        else:
            return '#FFB6C6'  # Light red

    def get_win_prob_color(self, prob):
        """Get text color for win probability (string: High/Medium/Low)."""
        prob_str = str(prob).lower()
        if prob_str == 'high':
            return '#008000'  # Dark green
        elif prob_str == 'medium':
            return '#FF8C00'  # Dark orange
        else:
            return '#D32F2F'  # Dark red

    def get_deadline_color(self, deadline_status):
        """Get text color for deadline status."""
        if deadline_status == 'urgent':
            return '#D32F2F'  # Red
        elif deadline_status == 'closing_soon':
            return '#FF8C00'  # Orange
        return '#000000'  # Black

    def format_intelligence_bar(self):
        """Generate 6-cell intelligence bar (colored cells, 4px tall)."""
        cells = [
            '#FF0000',  # Red
            '#FF7700',  # Orange
            '#FFFF00',  # Yellow
            '#00FF00',  # Green
            '#0000FF',  # Blue
            '#FF00FF',  # Magenta
        ]

        html = '<table style="width:100%; border-collapse:collapse; margin:16px 0;"><tr>'
        cell_width = 100 / len(cells)
        for color in cells:
            html += f'<td style="width:{cell_width}%; height:4px; background-color:{color}; border:none;"></td>'
        html += '</tr></table>'

        return html

    def generate_html(self):
        """Generate Outlook-compatible HTML email."""
        now = datetime.now()
        date_str = now.strftime('%B %d, %Y')
        summary = self.get_summary_stats()
        status_breakdown = self.get_status_breakdown()

        html_parts = []

        # HTML Header
        html_parts.append('''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: Arial, sans-serif; background-color:#f5f5f5; margin:0; padding:20px;">
<div style="max-width:800px; margin:0 auto; background-color:#ffffff; padding:20px; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1);">
''')

        # Header Section
        html_parts.append(f'''
<div style="text-align:center; border-bottom:2px solid #1a73e8; padding-bottom:16px; margin-bottom:20px;">
    <h1 style="margin:0 0 8px 0; color:#1a73e8; font-size:24px;">ClimateView RFP Intelligence</h1>
    <p style="margin:0; color:#666; font-size:14px;">Daily Digest • {date_str}</p>
</div>
''')

        # Intelligence Bar
        html_parts.append(self.format_intelligence_bar())

        # Summary Section
        html_parts.append(f'''
<div style="background-color:#f9f9f9; padding:16px; border-radius:4px; margin-bottom:20px;">
    <h2 style="margin:0 0 12px 0; color:#333; font-size:16px;">Summary</h2>
    <table style="width:100%; border-collapse:collapse;">
        <tr>
            <td style="padding:8px; border:1px solid #ddd;"><strong>Total Qualified</strong></td>
            <td style="padding:8px; border:1px solid #ddd; text-align:right;"><strong>{summary['qualified']}</strong></td>
        </tr>
        <tr style="background-color:#e8f5e9;">
            <td style="padding:8px; border:1px solid #ddd;"><strong>New Today</strong></td>
            <td style="padding:8px; border:1px solid #ddd; text-align:right;"><strong style="color:#2e7d32;">{summary['new']}</strong></td>
        </tr>
        <tr>
            <td style="padding:8px; border:1px solid #ddd;"><strong>High Probability (≥70%)</strong></td>
            <td style="padding:8px; border:1px solid #ddd; text-align:right;"><strong style="color:#1976d2;">{summary['high_prob']}</strong></td>
        </tr>
        <tr style="background-color:#ffebee;">
            <td style="padding:8px; border:1px solid #ddd;"><strong>Deadline Alerts (&lt;21 days)</strong></td>
            <td style="padding:8px; border:1px solid #ddd; text-align:right;"><strong style="color:#d32f2f;">{summary['alerts']}</strong></td>
        </tr>
    </table>
</div>
''')

        # New RFPs Section
        if self.new_rfps:
            html_parts.append(f'''
<div style="margin-bottom:20px;">
    <h2 style="margin:0 0 12px 0; color:#333; font-size:16px; border-left:4px solid #4caf50; padding-left:12px;">New RFPs Added ({len(self.new_rfps)})</h2>
    <table style="width:100%; border-collapse:collapse; border:1px solid #ddd;">
        <thead>
            <tr style="background-color:#f5f5f5;">
                <th style="padding:12px; text-align:left; border:1px solid #ddd; font-weight:bold; font-size:13px;">Title</th>
                <th style="padding:12px; text-align:left; border:1px solid #ddd; font-weight:bold; font-size:13px;">Entity</th>
                <th style="padding:12px; text-align:center; border:1px solid #ddd; font-weight:bold; font-size:13px;">Score</th>
                <th style="padding:12px; text-align:center; border:1px solid #ddd; font-weight:bold; font-size:13px;">Win Prob</th>
                <th style="padding:12px; text-align:center; border:1px solid #ddd; font-weight:bold; font-size:13px;">Deadline</th>
            </tr>
        </thead>
        <tbody>
''')
            for rfp in self.new_rfps:
                title = rfp.get('rfp_title', 'N/A')[:50]
                entity = rfp.get('issuing_entity', 'N/A')[:30]
                score = rfp.get('relevance_score', 0)
                win_prob = rfp.get('win_probability', 'N/A')
                deadline = rfp.get('deadline', 'N/A')[:10]
                score_color = self.get_score_color(score)
                win_prob_color = self.get_win_prob_color(win_prob)

                html_parts.append(f'''
            <tr style="border-bottom:1px solid #ddd;">
                <td style="padding:10px; border:1px solid #ddd; font-size:13px;">{title}</td>
                <td style="padding:10px; border:1px solid #ddd; font-size:13px;">{entity}</td>
                <td style="padding:10px; border:1px solid #ddd; text-align:center; background-color:{score_color}; font-weight:bold;">{score}</td>
                <td style="padding:10px; border:1px solid #ddd; text-align:center; color:{win_prob_color}; font-weight:bold;">{win_prob}</td>
                <td style="padding:10px; border:1px solid #ddd; text-align:center; font-size:13px;">{deadline}</td>
            </tr>
''')
            html_parts.append('        </tbody>\n    </table>\n</div>\n')

        # Deadline Alerts Section
        if self.deadline_alerts:
            html_parts.append(f'''
<div style="margin-bottom:20px;">
    <h2 style="margin:0 0 12px 0; color:#d32f2f; font-size:16px; border-left:4px solid #d32f2f; padding-left:12px;">⚠ Deadline Alerts ({len(self.deadline_alerts)})</h2>
    <table style="width:100%; border-collapse:collapse; border:1px solid #ddd;">
        <thead>
            <tr style="background-color:#ffebee;">
                <th style="padding:12px; text-align:left; border:1px solid #ddd; font-weight:bold; font-size:13px;">Title</th>
                <th style="padding:12px; text-align:left; border:1px solid #ddd; font-weight:bold; font-size:13px;">Deadline</th>
                <th style="padding:12px; text-align:center; border:1px solid #ddd; font-weight:bold; font-size:13px;">Days Left</th>
                <th style="padding:12px; text-align:center; border:1px solid #ddd; font-weight:bold; font-size:13px;">Status</th>
            </tr>
        </thead>
        <tbody>
''')
            now = datetime.now()
            for rfp in self.deadline_alerts:
                title = rfp.get('rfp_title', 'N/A')[:50]
                deadline = rfp.get('deadline', 'N/A')[:10]
                deadline_status = rfp.get('deadline_status', '')
                deadline_color = self.get_deadline_color(deadline_status)

                try:
                    deadline_date = parse_iso(rfp.get('deadline', ''))
                    days_left = (deadline_date - now).days
                except (ValueError, TypeError):
                    days_left = 'N/A'

                status_label = 'URGENT' if deadline_status == 'urgent' else 'CLOSING SOON'

                html_parts.append(f'''
            <tr style="border-bottom:1px solid #ddd;">
                <td style="padding:10px; border:1px solid #ddd; font-size:13px;">{title}</td>
                <td style="padding:10px; border:1px solid #ddd; font-size:13px;">{deadline}</td>
                <td style="padding:10px; border:1px solid #ddd; text-align:center; font-weight:bold; color:{deadline_color};">{days_left}</td>
                <td style="padding:10px; border:1px solid #ddd; text-align:center; color:{deadline_color}; font-weight:bold;">{status_label}</td>
            </tr>
''')
            html_parts.append('        </tbody>\n    </table>\n</div>\n')

        # Active RFPs Status Breakdown
        html_parts.append(f'''
<div style="background-color:#f9f9f9; padding:16px; border-radius:4px; margin-bottom:20px;">
    <h2 style="margin:0 0 12px 0; color:#333; font-size:16px;">Active RFPs by Status</h2>
    <table style="width:100%; border-collapse:collapse;">
        <tr>
            <td style="padding:8px; border:1px solid #ddd;"><strong>New</strong></td>
            <td style="padding:8px; border:1px solid #ddd; text-align:right;"><strong>{status_breakdown.get('New', 0)}</strong></td>
        </tr>
        <tr style="background-color:#f0f0f0;">
            <td style="padding:8px; border:1px solid #ddd;"><strong>Reviewing</strong></td>
            <td style="padding:8px; border:1px solid #ddd; text-align:right;"><strong>{status_breakdown.get('Reviewing', 0)}</strong></td>
        </tr>
        <tr>
            <td style="padding:8px; border:1px solid #ddd;"><strong>Shortlisted</strong></td>
            <td style="padding:8px; border:1px solid #ddd; text-align:right;"><strong>{status_breakdown.get('Shortlisted', 0)}</strong></td>
        </tr>
    </table>
</div>
''')

        # Footer Section
        html_parts.append(f'''
<div style="border-top:1px solid #ddd; padding-top:16px; margin-top:20px; text-align:center; color:#666; font-size:12px;">
    <p style="margin:0 0 8px 0;">
        <a href="{DASHBOARD_LINK}" style="color:#1a73e8; text-decoration:none; font-weight:bold;">View Full Dashboard</a>
    </p>
    <p style="margin:0;">Scoring Configuration v{SCORING_CONFIG_VERSION}</p>
    <p style="margin:8px 0 0 0; color:#999; font-size:11px;">
        Automated digest sent at {now.strftime('%H:%M:%S UTC')}
    </p>
</div>

</div>
</body>
</html>
''')

        return ''.join(html_parts)

    def get_subject(self):
        """Generate dynamic subject line."""
        now = datetime.now()
        date_str = now.strftime('%m/%d/%Y')
        alert_count = len(self.deadline_alerts)
        high_prob_count = len(self.high_prob_rfps)

        if alert_count > 0:
            return f"⚠ {alert_count} urgent RFP(s) – ClimateView Daily Digest {date_str}"
        elif high_prob_count > 0:
            return f"🟢 {high_prob_count} high-probability RFP(s) found – Daily Digest {date_str}"
        else:
            return f"ClimateView RFP Digest – {date_str}"

    def send_email(self, smtp_host, smtp_port, smtp_user, smtp_pass, recipient):
        """Send digest email via SMTP."""
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = self.get_subject()
            msg['From'] = smtp_user
            msg['To'] = recipient

            html_content = self.generate_html()
            msg.attach(MIMEText(html_content, 'html'))

            logger.info(f"Connecting to SMTP server: {smtp_host}:{smtp_port}")
            with smtplib.SMTP(smtp_host, int(smtp_port)) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)

            logger.info(f"Digest sent successfully to {recipient}")
            return True

        except smtplib.SMTPException as e:
            logger.error(f"SMTP error: {e}")
            return False
        except Exception as e:
            logger.error(f"Email sending failed: {e}")
            return False

    def run(self, force=False, preview=False, test=False):
        """Run the digest generator."""
        if test:
            self.load_test_data()
            logger.info("Using test data")
        else:
            if not self.load_rfp_data():
                logger.error("Could not load RFP data")
                sys.exit(0)

        self.load_last_run()
        self.process_rfps()

        if not self.has_updates() and not force:
            logger.info("No updates, skipping digest")
            return True

        html = self.generate_html()

        if preview:
            print(html)
            logger.info("Preview mode: HTML printed to stdout")
            return True

        # Try to send via SMTP
        smtp_host = os.environ.get('SMTP_HOST')
        smtp_port = os.environ.get('SMTP_PORT', '587')
        smtp_user = os.environ.get('SMTP_USER')
        smtp_pass = os.environ.get('SMTP_PASS')
        recipient = os.environ.get('DIGEST_RECIPIENT', DEFAULT_RECIPIENT)

        if smtp_host and smtp_user and smtp_pass:
            success = self.send_email(smtp_host, smtp_port, smtp_user, smtp_pass, recipient)
            if not success:
                logger.error("Failed to send digest via SMTP")
                sys.exit(1)
        else:
            logger.warning("SMTP credentials not configured, printing HTML to stdout")
            print(html)
            return True

        self.save_last_run()
        return True

    def load_test_data(self):
        """Load built-in test data."""
        now = datetime.now()
        one_day_ago = (now - timedelta(days=1)).isoformat()
        five_days_from_now = (now + timedelta(days=5)).isoformat()
        ten_days_from_now = (now + timedelta(days=10)).isoformat()
        thirty_days_from_now = (now + timedelta(days=30)).isoformat()

        self.rfp_data = [
            {
                'id': 'test-001',
                'rfp_title': 'Climate Action Plan – GHG Platform',
                'issuing_entity': 'City of Portland',
                'country': 'US',
                'qualified': True,
                'added_date': one_day_ago,
                'last_updated': one_day_ago,
                'deadline': five_days_from_now,
                'deadline_status': 'urgent',
                'relevance_score': 72,
                'win_probability': 'High',
                'status': 'New',
                'rfp_type': 'platform',
            },
            {
                'id': 'test-002',
                'rfp_title': 'Klimaschutzkonzept Beratung und Plattform',
                'issuing_entity': 'Stadt Dusseldorf',
                'country': 'DE',
                'qualified': True,
                'added_date': one_day_ago,
                'last_updated': one_day_ago,
                'deadline': ten_days_from_now,
                'deadline_status': 'closing_soon',
                'relevance_score': 62,
                'win_probability': 'Medium',
                'status': 'New',
                'rfp_type': 'consulting_with_platform',
            },
            {
                'id': 'test-003',
                'rfp_title': 'Net Zero Roadmap Software',
                'issuing_entity': 'Scottish Government',
                'country': 'GB',
                'qualified': True,
                'added_date': (now - timedelta(days=10)).isoformat(),
                'last_updated': (now - timedelta(days=10)).isoformat(),
                'deadline': thirty_days_from_now,
                'deadline_status': 'open',
                'relevance_score': 58,
                'win_probability': 'Medium',
                'status': 'Reviewing',
                'rfp_type': 'platform',
            },
        ]
        logger.info("Loaded test data with 3 RFPs")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description='ClimateView RFP Digest Sender'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Send digest even if no updates'
    )
    parser.add_argument(
        '--preview',
        action='store_true',
        help='Print HTML to stdout without sending'
    )
    parser.add_argument(
        '--test',
        action='store_true',
        help='Run with built-in test data'
    )

    args = parser.parse_args()

    digest = RFPDigest()
    try:
        success = digest.run(
            force=args.force,
            preview=args.preview,
            test=args.test
        )
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
