# ClimateView RFP Tracking System - Deployment Guide

## Prerequisites

- GitHub account (free tier sufficient)
- SAM.gov API key (free, requires registration at [api.sam.gov](https://api.sam.gov))
- SMTP credentials for sending email digests:
  - Gmail app password (free, requires [enabling 2FA](https://support.google.com/accounts/answer/185833))
  - OR SendGrid free tier (100 emails/day free at [sendgrid.com](https://sendgrid.com))
  - OR other SMTP provider (Mailgun, Brevo, etc.)

## Setup Steps

### 1. Create GitHub Repository

- Go to [github.com/new](https://github.com/new)
- Repository name: `climateview-rfp-tracker` (or similar)
- Description: "Automated RFP scanner for climate-related procurement opportunities"
- Select **Private** (unlisted repository)
- Create repository

### 2. Clone and Add Files

```bash
git clone https://github.com/YOUR_USERNAME/climateview-rfp-tracker.git
cd climateview-rfp-tracker
```

Add the following files to the repository:
- `rfp_scanner.py` - Main scanning logic (SAM.gov, TED, UK Contracts Finder)
- `rfp_scorer.py` - Scoring engine with v1.1 schema
- `rfp_scoring_config.json` - Configuration for scoring rules and weights
- `send_digest.py` - Email digest generation and sending
- `index.html` - Dashboard for viewing RFP data
- `rfp_data.json` - RFP records (provided)

Create directory structure:
```bash
mkdir -p .github/workflows
```

### 3. Enable GitHub Pages

1. Go to repository **Settings** → **Pages**
2. Under "Build and deployment":
   - Source: **Deploy from a branch**
   - Branch: **main**, folder: **/ (root)**
3. Click **Save**
4. Wait 1-2 minutes for GitHub Pages to build
5. Note the URL: `https://YOUR_USERNAME.github.io/climateview-rfp-tracker/`

### 4. Add Repository Secrets

1. Go to repository **Settings** → **Secrets and variables** → **Actions**
2. Add the following secrets (click **New repository secret** for each):

| Secret Name | Value | Example |
|---|---|---|
| `SAM_API_KEY` | Your SAM.gov API key | `AbCdEfGhIjKlMnOpQrStUvWxYz` |
| `SMTP_HOST` | SMTP server hostname | `smtp.gmail.com` |
| `SMTP_PORT` | SMTP port (usually 587) | `587` |
| `SMTP_USER` | SMTP username/email | `your-email@gmail.com` |
| `SMTP_PASS` | SMTP password or app password | `xxxx xxxx xxxx xxxx` |
| `DIGEST_RECIPIENT` | Email to receive digests | `your-email@example.com` |

**Gmail SMTP Example:**
```
SMTP_HOST: smtp.gmail.com
SMTP_PORT: 587
SMTP_USER: your-email@gmail.com
SMTP_PASS: (16-character app password from https://myaccount.google.com/apppasswords)
```

**SendGrid SMTP Example:**
```
SMTP_HOST: smtp.sendgrid.net
SMTP_PORT: 587
SMTP_USER: apikey
SMTP_PASS: (your SendGrid API key)
```

### 5. Add GitHub Actions Workflow

1. Create `.github/workflows/scan.yml` with the provided workflow configuration
2. Commit and push:
```bash
git add .github/workflows/scan.yml
git commit -m "Add RFP scanning workflow"
git push origin main
```

### 6. Enable GitHub Actions

1. Go to repository **Settings** → **Actions** → **General**
2. Under "Actions permissions", select **Allow all actions and reusable workflows**
3. Click **Save**

### 7. Initial Commit

```bash
git add .
git commit -m "Initial ClimateView RFP tracker setup"
git push origin main
```

## How It Works

### Automated Scanning

- **Schedule**: Runs daily at 06:00 UTC (configurable via `cron` in `scan.yml`)
- **Trigger**: Can also be triggered manually via GitHub Actions UI

### Scanner Process

1. **Fetch RFPs** from multiple sources:
   - SAM.gov (US federal opportunities)
   - TED (European tenders)
   - UK Contracts Finder (UK government contracts)

2. **Filter** for climate-related keywords:
   - "climate", "emissions", "net zero", "carbon", "renewable", "sustainability", etc.

3. **Score** each RFP using the v1.1 schema:
   - Feature alignment (how well does ClimateView fit?)
   - Geographic fit (target markets)
   - Budget fit (profitable opportunity?)
   - Timeline fit (feasible deadline?)
   - Competitive landscape (Kausal, C40, others?)
   - Strategic value (long-term partner potential?)

4. **Update** `rfp_data.json` with new/updated records

5. **Send** email digest:
   - Only if new RFPs or significant changes detected
   - Summary of qualified opportunities
   - Scoring breakdown and recommendations

6. **Commit** changes to repository (automatically pushes)

### Dashboard Access

- URL: `https://YOUR_USERNAME.github.io/climateview-rfp-tracker/`
- Loads `rfp_data.json` from the repository
- Real-time display of all RFP records
- Interactive scoring and filtering
- Unlisted repository means not indexed by search engines (private but accessible via direct link)

## Configuration

### Scoring Rules

Edit `rfp_scoring_config.json` to adjust scoring weights:

```json
{
  "version": "1.1",
  "scoring_weights": {
    "feature_alignment": 0.25,
    "geographic_fit": 0.20,
    "budget_fit": 0.20,
    "timeline_score": 0.15,
    "competitive_score": 0.15,
    "strategic_value": 0.05
  },
  "keywords": {
    "climate": ["climate", "emissions", "carbon"],
    "renewable": ["renewable", "solar", "wind"],
    "net_zero": ["net zero", "net-zero", "carbon neutral"]
  },
  "disqualification_keywords": [
    "construction",
    "physical infrastructure",
    "procurement",
    "equipment purchase"
  ],
  "confidence_thresholds": {
    "high": 0.8,
    "medium": 0.5,
    "low": 0
  }
}
```

### Scanner Configuration

Edit `rfp_scanner.py` to:
- Add or remove portal sources
- Adjust search keywords and filters
- Modify frequency of checks
- Add custom filtering logic

### Email Digest Customization

Edit `send_digest.py` to:
- Change email template and styling
- Adjust thresholds for sending digest
- Add recipient groups
- Customize summary metrics

## Manual Operations

### Run Scanner Manually

1. Go to repository **Actions** tab
2. Click **RFP Scanner** workflow on the left
3. Click **Run workflow** dropdown on the right
4. Select branch: **main**
5. Click **Run workflow**
6. Check logs in the workflow run details

### Check Workflow Logs

1. Go to **Actions** tab
2. Click the latest workflow run
3. Click the **scan** job to see console output
4. Useful for debugging API issues, parsing errors, or SMTP problems

### View Dashboard

- Open `https://YOUR_USERNAME.github.io/climateview-rfp-tracker/`
- Dashboard reads from `rfp_data.json` in the repository
- Refresh page to see latest data (no caching)

### Update Data Manually

```bash
# Edit rfp_data.json locally
# Then commit and push
git add rfp_data.json
git commit -m "Manual RFP data update"
git push origin main
```

## Cost Analysis

**Everything is completely free:**

| Component | Service | Cost |
|---|---|---|
| Repository hosting | GitHub | Free (unlimited private repos) |
| Scheduled workflows | GitHub Actions | Free (2,000 min/month) |
| Dashboard hosting | GitHub Pages | Free (unlimited bandwidth) |
| RFP data sources | SAM.gov | Free (registration required, no charges) |
| | TED | Free (no API key needed) |
| | UK Contracts Finder | Free (no authentication needed) |
| Email delivery | Gmail SMTP | Free (with 2FA and app password) |
| | SendGrid | Free (100 emails/day) |
| Domain | (optional) | ~$12/year if custom domain |

**Total monthly cost: $0** (excluding optional custom domain)

## Troubleshooting

### Workflow Not Running

- **Issue**: Scheduled workflow hasn't run
- **Solution**:
  - GitHub Actions requires at least one commit in the last 60 days
  - Manually trigger via Actions tab to test
  - Check repository settings → Actions → All workflows enabled

### Scan Job Failed

- **Check logs**: Actions tab → workflow run → scan job
- **Common errors**:
  - `SAM_API_KEY not found` → Add secret to Settings → Secrets
  - `requests.ConnectionError` → Portal API may be down (check status pages)
  - `JSON decode error` → Parser may need updating if portal API changed

### Email Not Sent

- **Check logs**: Actions tab → workflow run → send_digest step
- **Common errors**:
  - `SMTP authentication failed` → Verify SMTP credentials in secrets
  - `Connection refused` → Check SMTP_HOST and SMTP_PORT are correct
  - `timeout` → SMTP server unreachable; check network/firewall
- **Test SMTP manually**:
  ```bash
  python -c "import smtplib; smtplib.SMTP('smtp.gmail.com', 587).starttls()"
  ```

### No RFPs Found

- **Issue**: Scan completes but `rfp_data.json` shows no new records
- **Causes**:
  - Portal APIs changed (common for government sites)
  - Keywords don't match current portal content
  - Portal may require authentication now
- **Fix**:
  - Check portal websites manually for climate RFPs
  - Update keywords in `rfp_scanner.py`
  - Check API documentation if available

### Dashboard Shows "No Data"

- **Issue**: Dashboard loads but no RFPs visible
- **Causes**:
  - `rfp_data.json` missing or empty
  - Incorrect GitHub Pages path
  - Browser caching old version
- **Fix**:
  - Verify `rfp_data.json` exists in repository root
  - Check GitHub Pages settings (should be `/ (root)`)
  - Hard refresh browser (Ctrl+Shift+R or Cmd+Shift+R)
  - Check browser console for CORS errors

### Portal API Rate Limiting

- **Issue**: Scanner fails with 429 Too Many Requests
- **Solution**:
  - Add delays between API calls in `rfp_scanner.py`
  - Reduce frequency of scans (change cron schedule)
  - Use pagination to fetch results in smaller batches

### Commit Failed (No Changes)

- **Issue**: Git commit fails with "nothing to commit"
- **Explanation**: This is normal when no new RFPs are found
- **Behavior**: Workflow completes successfully, no changes pushed
- **Note**: Only commits when `rfp_data.json` or `.digest_last_run` actually changed

## Advanced Configuration

### Custom Dashboard Branding

Edit `index.html` to customize:
- Color scheme and logos
- Company branding
- Dashboard layout and sections
- Export functionality

### Additional Data Sources

Add more RFP portals in `rfp_scanner.py`:
- GovUK Find a Tender (FaT)
- OpenTender.net
- Regional government procurement sites
- Industry-specific tender boards

### Slack/Teams Notifications

Modify `send_digest.py` to send notifications to:
- Slack channel via webhook
- Microsoft Teams
- Discord
- Custom webhook endpoints

### Database Integration

Extend to store RFP history:
- SQLite for local storage
- PostgreSQL for shared access
- Google Sheets for team collaboration

## Security Considerations

- **Repository Privacy**: Keep repository private to avoid exposing SMTP credentials in workflow logs
- **Secret Rotation**: Rotate SMTP passwords periodically
- **API Key Management**: Regenerate SAM.gov API key if compromised
- **GitHub Pages**: Repository is unlisted but accessible via direct URL - don't publicize link if sensitive
- **No Personal Data**: Don't store PII or internal company information in RFP records

## Support & Maintenance

### Regular Maintenance

- **Monthly**: Review and update scoring thresholds based on false positives/negatives
- **Quarterly**: Audit disqualification rules and adjust if business focus changes
- **As needed**: Update parser logic if government portals change their HTML/API structure

### Monitoring Health

- Subscribe to workflow notifications (GitHub settings)
- Periodically check Actions tab for failed runs
- Monitor email delivery (track digest send frequency)

### Reporting Issues

If you encounter issues:
1. Check the relevant workflow logs (Actions tab)
2. Verify all secrets are correctly configured
3. Test portal APIs manually to confirm they're accessible
4. Review console logs for specific error messages

---

**Initial Setup Time**: 15-20 minutes
**Ongoing Maintenance**: 5-10 minutes per week
**Value**: Continuous, automated identification of climate RFP opportunities
