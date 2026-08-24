/**
 * Report fixtures for tests.
 *
 * `helpscoutReport` is REAL DATA, taken verbatim from the Epic 5/6 live
 * verification run held in avp_dev — scan_01M0HDRGJNWNZDSJPP0NC3SV8W, a genuine
 * scan of helpscout.com with real competitor detection, real engine results and
 * a real technical audit. It is checked in rather than synthesised so the
 * derivation is tested against the shapes the pipeline actually produces,
 * including the awkward ones: a citation strength of 3.70, a NO_AUTHORITY_DATA
 * degradation flag, and an audit whose only findings are two warnings.
 *
 * The degraded variants below are derived from it by removing data, which is
 * exactly how the degraded cases arise in production.
 */

import type { Report } from '@avp/shared-types';

export const helpscoutReport = {
  "scanId": "scan_01M0HDRGJNWNZDSJPP0NC3SV8W",
  "scanStatus": "succeeded",
  "generatedAt": "2026-08-22T00:29:49.591713Z",
  "scannedAt": "2026-08-21T05:49:24.664644Z",
  "agency": {
    "id": "agcy_01M0HDRGGZWW01DSWW2VHMAZH4",
    "name": "Scoring Verification",
    "slug": "scoring-2vhmazh5"
  },
  "subject": {
    "clientId": "clnt_01M0HDRGJBPRQX4YCBKFEEVYDV",
    "name": "Help Scout",
    "domain": "helpscout.com",
    "industry": "customer support software",
    "industryNiche": null,
    "brandName": "Help Scout"
  },
  "score": {
    "id": "scor_01M0HDVVBQSMPKEX1JBGCBJ1NT",
    "scanId": "scan_01M0HDRGJNWNZDSJPP0NC3SV8W",
    "status": "scored",
    "composite": "58.24",
    "mentionRate": "100.00",
    "shareOfVoice": "30.00",
    "citationStrength": "3.70",
    "sentiment": "75.00",
    "technicalFoundation": "87.50",
    "formulaVersion": "v1.1",
    "weights": {
      "sentiment": "15.00",
      "mention_rate": "30.00",
      "share_of_voice": "25.00",
      "citation_strength": "20.00",
      "technical_foundation": "10.00"
    },
    "excludedDimensions": {},
    "degradationFlags": [
      "NO_AUTHORITY_DATA"
    ],
    "reasonCode": null,
    "inputsDigest": "a6b8ebef36f71dc4679bef86c2ec04ed6802126626c2c7bbb0edb4f1ee68d0b5",
    "computedAt": "2026-08-21T23:45:57.897527Z",
    "createdAt": "2026-08-21T05:49:24.711366Z",
    "updatedAt": "2026-08-21T23:45:57.891366Z",
    "competitors": [
      {
        "competitorId": "comp_01M0HDSXA4KYP8YKWY2HBEDZFX",
        "name": "Zendesk",
        "mentionRate": "83.33",
        "shareOfVoice": "25.00",
        "citationStrength": "3.70"
      },
      {
        "competitorId": "comp_01M0HDSXA4KYP8YKWY2HBEDZFY",
        "name": "Freshdesk",
        "mentionRate": "66.67",
        "shareOfVoice": "20.00",
        "citationStrength": "0.00"
      },
      {
        "competitorId": "comp_01M0HDSXA4KYP8YKWY2HBEDZFZ",
        "name": "Front",
        "mentionRate": "83.33",
        "shareOfVoice": "25.00",
        "citationStrength": "3.70"
      },
      {
        "competitorId": "comp_01M0HDSXA4KYP8YKWY2HBEDZG0",
        "name": "Kustomer",
        "mentionRate": "0.00",
        "shareOfVoice": "0.00",
        "citationStrength": "0.00"
      },
      {
        "competitorId": "comp_01M0HDSXA4KYP8YKWY2HBEDZG1",
        "name": "Thecxlead",
        "mentionRate": "0.00",
        "shareOfVoice": "0.00",
        "citationStrength": "0.00"
      }
    ]
  },
  "dimensions": [
    {
      "key": "mention_rate",
      "weight": "30.00",
      "subscore": "100.00",
      "included": true,
      "exclusionReason": null
    },
    {
      "key": "share_of_voice",
      "weight": "25.00",
      "subscore": "30.00",
      "included": true,
      "exclusionReason": null
    },
    {
      "key": "citation_strength",
      "weight": "20.00",
      "subscore": "3.70",
      "included": true,
      "exclusionReason": null
    },
    {
      "key": "sentiment",
      "weight": "15.00",
      "subscore": "75.00",
      "included": true,
      "exclusionReason": null
    },
    {
      "key": "technical_foundation",
      "weight": "10.00",
      "subscore": "87.50",
      "included": true,
      "exclusionReason": null
    }
  ],
  "competitorSet": {
    "status": "ok",
    "detectionConfidence": "0.800",
    "confidenceCovers": 5,
    "competitors": [
      {
        "id": "comp_01M0HDSXA4KYP8YKWY2HBEDZFX",
        "name": "Zendesk",
        "domain": "zendesk.com",
        "rank": 1,
        "detectionSource": "both",
        "serpMentions": 4,
        "coCitationMentions": 1,
        "corroborated": true,
        "signalCount": 5,
        "score": "3.520",
        "isManualOverride": false,
        "mentionRate": "83.33",
        "shareOfVoice": "25.00",
        "citationStrength": "3.70"
      },
      {
        "id": "comp_01M0HDSXA4KYP8YKWY2HBEDZFY",
        "name": "Freshdesk",
        "domain": "freshworks.com",
        "rank": 2,
        "detectionSource": "both",
        "serpMentions": 2,
        "coCitationMentions": 1,
        "corroborated": true,
        "signalCount": 3,
        "score": "2.043",
        "isManualOverride": false,
        "mentionRate": "66.67",
        "shareOfVoice": "20.00",
        "citationStrength": "0.00"
      },
      {
        "id": "comp_01M0HDSXA4KYP8YKWY2HBEDZFZ",
        "name": "Front",
        "domain": "front.com",
        "rank": 3,
        "detectionSource": "both",
        "serpMentions": 3,
        "coCitationMentions": 1,
        "corroborated": true,
        "signalCount": 4,
        "score": "1.679",
        "isManualOverride": false,
        "mentionRate": "83.33",
        "shareOfVoice": "25.00",
        "citationStrength": "3.70"
      },
      {
        "id": "comp_01M0HDSXA4KYP8YKWY2HBEDZG0",
        "name": "Kustomer",
        "domain": "kustomer.com",
        "rank": 4,
        "detectionSource": "both",
        "serpMentions": 2,
        "coCitationMentions": 1,
        "corroborated": true,
        "signalCount": 3,
        "score": "1.317",
        "isManualOverride": false,
        "mentionRate": "0.00",
        "shareOfVoice": "0.00",
        "citationStrength": "0.00"
      },
      {
        "id": "comp_01M0HDSXA4KYP8YKWY2HBEDZG1",
        "name": "Thecxlead",
        "domain": "thecxlead.com",
        "rank": 5,
        "detectionSource": "serp",
        "serpMentions": 3,
        "coCitationMentions": 0,
        "corroborated": false,
        "signalCount": 3,
        "score": "0.889",
        "isManualOverride": false,
        "mentionRate": "0.00",
        "shareOfVoice": "0.00",
        "citationStrength": "0.00"
      }
    ]
  },
  "proof": {
    "promptsRun": 3,
    "engineResults": 6,
    "answeredResults": 6,
    "resultsMentioningSubject": 6,
    "engineCoverage": [
      {
        "engine": "claude",
        "promptsRun": 3,
        "answered": 3,
        "mentioned": 3
      },
      {
        "engine": "claude_search",
        "promptsRun": 3,
        "answered": 3,
        "mentioned": 3
      }
    ],
    "totalCitations": 45,
    "subjectCitations": 3,
    "subjectCitedDomains": [
      {
        "domain": "helpscout.com",
        "citations": 3,
        "citesSubject": true,
        "competitorName": null,
        "sampleUrl": "https://docs.helpscout.com/article/596-billing-and-plans-guide"
      }
    ],
    "competitorCitedDomains": [
      {
        "domain": "front.com",
        "citations": 1,
        "citesSubject": false,
        "competitorName": "Front",
        "sampleUrl": "https://front.com/blog/help-scout-alternatives"
      },
      {
        "domain": "zendesk.com",
        "citations": 1,
        "citesSubject": false,
        "competitorName": "Zendesk",
        "sampleUrl": "https://www.zendesk.com/service/comparison/help-scout-alternatives/"
      },
      {
        "domain": "eesel.ai",
        "citations": 6,
        "citesSubject": false,
        "competitorName": null,
        "sampleUrl": "https://www.eesel.ai/blog/helpscout-pricing"
      },
      {
        "domain": "featurebase.app",
        "citations": 4,
        "citesSubject": false,
        "competitorName": null,
        "sampleUrl": "https://www.featurebase.app/blog/shared-inbox-software"
      },
      {
        "domain": "hiverhq.com",
        "citations": 4,
        "citesSubject": false,
        "competitorName": null,
        "sampleUrl": "https://hiverhq.com/blog/help-scout-pricing"
      },
      {
        "domain": "gorgias.com",
        "citations": 3,
        "citesSubject": false,
        "competitorName": null,
        "sampleUrl": "https://www.gorgias.com/blog/help-scout-pricing"
      },
      {
        "domain": "checkthat.ai",
        "citations": 2,
        "citesSubject": false,
        "competitorName": null,
        "sampleUrl": "https://checkthat.ai/brands/help-scout/pricing"
      },
      {
        "domain": "getmacha.com",
        "citations": 2,
        "citesSubject": false,
        "competitorName": null,
        "sampleUrl": "https://www.getmacha.com/blog/help-scout-pricing-explained"
      },
      {
        "domain": "bolddesk.com",
        "citations": 1,
        "citesSubject": false,
        "competitorName": null,
        "sampleUrl": "https://www.bolddesk.com/blogs/zendesk-pricing"
      },
      {
        "domain": "canadacreate.com",
        "citations": 1,
        "citesSubject": false,
        "competitorName": null,
        "sampleUrl": "https://canadacreate.com/top-10-shared-inbox-tools-for-effortless-team-email-management-in-2025/"
      },
      {
        "domain": "clearfeed.ai",
        "citations": 1,
        "citesSubject": false,
        "competitorName": null,
        "sampleUrl": "https://clearfeed.ai/blogs/understanding-zendesk-pricing"
      },
      {
        "domain": "desk365.io",
        "citations": 1,
        "citesSubject": false,
        "competitorName": null,
        "sampleUrl": "https://www.desk365.io/blog/zendesk-pricing/"
      }
    ],
    "mentionShares": [
      {
        "entityName": "Help Scout",
        "entityDomain": "helpscout.com",
        "isSubject": true,
        "appearances": 6,
        "bestPosition": 1,
        "outranksSubject": false
      },
      {
        "entityName": "Front",
        "entityDomain": "front.com",
        "isSubject": false,
        "appearances": 5,
        "bestPosition": 2,
        "outranksSubject": false
      },
      {
        "entityName": "Zendesk",
        "entityDomain": "zendesk.com",
        "isSubject": false,
        "appearances": 5,
        "bestPosition": 2,
        "outranksSubject": false
      },
      {
        "entityName": "Freshdesk",
        "entityDomain": "freshworks.com",
        "isSubject": false,
        "appearances": 4,
        "bestPosition": 3,
        "outranksSubject": false
      }
    ]
  },
  "audit": {
    "status": "ok",
    "errorCode": null,
    "urlAudited": "https://helpscout.com",
    "technicalFoundation": "87.50",
    "auditedAt": "2026-08-21T23:45:57.877709Z",
    "passed": 14,
    "warned": 2,
    "failed": 0,
    "notApplicable": 1,
    "findings": [
      {
        "id": "tchk_01M0KBF2KVDGBFGQ71NSHEDKTT",
        "checkKey": "schema_faq",
        "status": "warn",
        "value": null,
        "detailCode": "NO_FAQ_SCHEMA"
      },
      {
        "id": "tchk_01M0KBF2KVDGBFGQ71NSHEDKTV",
        "checkKey": "schema_product_or_service",
        "status": "warn",
        "value": null,
        "detailCode": "NO_PRODUCT_OR_SERVICE_SCHEMA"
      }
    ]
  }
} as unknown as Report;

/**
 * The SAME scan, with Epic 8's generated fix list attached.
 *
 * Also real data: these five rows are what `POST /scans/{id}/fixes` actually
 * wrote to `action_items` in avp_dev on 2026-08-22, from a live claude-opus-5
 * call against the facts above. Checked in for the same reason the report
 * itself is — so the merge is tested against wording a model really produced,
 * including the parts that are awkward for a renderer: apostrophes, long
 * titles, and a detail that names another fix on the list.
 */
export const generatedFixesReport: Report = {
  ...helpscoutReport,
  actionItems: [
  {
    "id": "acti_01M0KXH410ZJC5Q5VX5QPGFG37",
    "scanId": "scan_01M0HDRGJNWNZDSJPP0NC3SV8W",
    "source": "gap",
    "sourceKey": "citation_strength",
    "title": "Publish comparison and 'best help desk software' reference pages on helpscout.com that AI answers can cite directly",
    "detail": "Across the six answers, 45 citations were collected and only 3 pointed at helpscout.com; the sources doing the work are third-party roundups such as eesel.ai, featurebase.app and hiverhq.com. Owned pages that answer the same comparison and category questions those roundups answer give the answer engines a first-party source to cite, and they are the same pages that would carry the missing FAQ and product markup.",
    "priority": "high",
    "effort": "L",
    "status": "open",
    "dimensionKey": "citation_strength",
    "pointsUpside": "19.26",
    "rank": 1,
    "generatedBy": "claude-opus-5",
    "createdAt": "2026-08-22T05:01:18.740904Z",
    "updatedAt": "2026-08-22T05:13:04.394102Z"
  },
  {
    "id": "acti_01M0KXH41131XQ9BF9RRPQZ1VJ",
    "scanId": "scan_01M0HDRGJNWNZDSJPP0NC3SV8W",
    "source": "gap",
    "sourceKey": "share_of_voice",
    "title": "Build side-by-side pages covering Help Scout against Zendesk, Freshdesk, Front and Kustomer",
    "detail": "Help Scout is named in all six answers, but shares those answers with Zendesk, Freshdesk, Front and Kustomer, leaving share of voice at 30 out of 100. Pages that set out where Help Scout fits relative to each named competitor give answer engines subject-specific material to draw on in the same responses, and feed the citation gap above.",
    "priority": "high",
    "effort": "M",
    "status": "open",
    "dimensionKey": "share_of_voice",
    "pointsUpside": "17.50",
    "rank": 2,
    "generatedBy": "claude-opus-5",
    "createdAt": "2026-08-22T05:01:18.740904Z",
    "updatedAt": "2026-08-22T05:13:04.394102Z"
  },
  {
    "id": "acti_01M0KXH412FTPC3YNBW5B07T60",
    "scanId": "scan_01M0HDRGJNWNZDSJPP0NC3SV8W",
    "source": "gap",
    "sourceKey": "sentiment",
    "title": "Add evidence pages covering support outcomes, migration and pricing detail to strengthen how Help Scout is characterised",
    "detail": "Sentiment scored 75 out of 100, so the brand is described favourably but not uniformly. Documented customer outcomes, migration guidance and clear pricing explanations on helpscout.com give the answer engines first-party material to characterise the product from, rather than relying on third-party roundups.",
    "priority": "medium",
    "effort": "M",
    "status": "open",
    "dimensionKey": "sentiment",
    "pointsUpside": "3.75",
    "rank": 3,
    "generatedBy": "claude-opus-5",
    "createdAt": "2026-08-22T05:01:18.740904Z",
    "updatedAt": "2026-08-22T05:13:04.394102Z"
  },
  {
    "id": "acti_01M0KXH412FTPC3YNBW5B07T61",
    "scanId": "scan_01M0HDRGJNWNZDSJPP0NC3SV8W",
    "source": "audit",
    "sourceKey": "schema_faq",
    "title": "Add FAQPage markup to the pages that answer buyer questions about Help Scout",
    "detail": "The site currently declares Corporation, VideoObject and WebSite types only, with no FAQ markup. Marking up the question-and-answer sections of the comparison and category pages described above makes those answers machine-readable at the point where the citation gap is being addressed.",
    "priority": "medium",
    "effort": "S",
    "status": "open",
    "dimensionKey": "technical_foundation",
    "pointsUpside": null,
    "rank": 4,
    "generatedBy": "claude-opus-5",
    "createdAt": "2026-08-22T05:01:18.740904Z",
    "updatedAt": "2026-08-22T05:13:04.394102Z"
  },
  {
    "id": "acti_01M0KXH412FTPC3YNBW5B07T62",
    "scanId": "scan_01M0HDRGJNWNZDSJPP0NC3SV8W",
    "source": "audit",
    "sourceKey": "schema_product_or_service",
    "title": "Add Product or SoftwareApplication markup describing the Help Scout product and its plans",
    "detail": "No product or service type is declared among the Corporation, VideoObject and WebSite markup found. Declaring the product and its plans explicitly lets answer engines tie the crawled 877-word homepage content to a named software product rather than only to a company entity.",
    "priority": "medium",
    "effort": "S",
    "status": "open",
    "dimensionKey": "technical_foundation",
    "pointsUpside": null,
    "rank": 5,
    "generatedBy": "claude-opus-5",
    "createdAt": "2026-08-22T05:01:18.740904Z",
    "updatedAt": "2026-08-22T05:13:04.394102Z"
  }
],
} as unknown as Report;

/**
 * The same scan after an operator corrected the competitor set — Epic 3.6.
 *
 * Derived by flipping `isManualOverride` and `detectionSource` on one rival and
 * clearing `detectionConfidence`, which is exactly what
 * `PUT /clients/{id}/competitors` does: it replaces the set wholesale, marks
 * every entry manual, and drops the corroboration figure because that number
 * measures agreement between two automated signals and neither produced the
 * corrected set.
 */
export const manualOverrideReport: Report = {
  ...helpscoutReport,
  competitorSet: {
    ...helpscoutReport.competitorSet!,
    detectionConfidence: null,
    // Four of the five rows are still detection's. The figure is null here, so
    // nothing is scoped by it — but the count stays truthful about the rows
    // rather than being zeroed alongside the confidence.
    confidenceCovers: 4,
    competitors: helpscoutReport.competitorSet!.competitors.map((competitor, index) =>
      index === 0
        ? { ...competitor, isManualOverride: true, detectionSource: 'manual' }
        : competitor,
    ),
  },
} as unknown as Report;

/** A scan that ran but produced nothing scoreable — composite null, not zero. */
export const insufficientDataReport: Report = {
  ...helpscoutReport,
  score: {
    ...helpscoutReport.score!,
    status: 'insufficient_data',
    composite: null,
    mentionRate: null,
    shareOfVoice: null,
    citationStrength: null,
    sentiment: null,
    technicalFoundation: null,
    reasonCode: 'INSUFFICIENT_DATA',
    degradationFlags: ['NO_ANSWERED_RESULTS'],
    excludedDimensions: {
      mention_rate: 'NO_ANSWERED_RESULTS',
      share_of_voice: 'NO_ANSWERED_RESULTS',
      citation_strength: 'NO_ANSWERED_RESULTS',
      sentiment: 'NO_ANSWERED_RESULTS',
      technical_foundation: 'NO_ANSWERED_RESULTS',
    },
    competitors: [],
  },
  dimensions: helpscoutReport.dimensions.map((d) => ({
    ...d,
    subscore: null,
    included: false,
    exclusionReason: 'NO_ANSWERED_RESULTS',
  })),
  competitorSet: null,
  audit: null,
  proof: {
    ...helpscoutReport.proof,
    engineResults: 0,
    answeredResults: 0,
    resultsMentioningSubject: 0,
    engineCoverage: [],
    totalCitations: 0,
    subjectCitations: 0,
    subjectCitedDomains: [],
    competitorCitedDomains: [],
    mentionShares: [],
  },
};

/** Competitor detection came back empty; Share of Voice excluded and redistributed. */
export const noCompetitorSetReport: Report = {
  ...helpscoutReport,
  score: {
    ...helpscoutReport.score!,
    // 40.00x100 + 26.67x3.70 + 20.00x75 + 13.33x87.50, all over 100. The
    // redistributed weights are what make this HIGHER than the 58.24 the full
    // five-dimension score produced: the weight Share of Voice was carrying
    // (a 30/100 sub-score) moves onto dimensions that score better.
    composite: '67.65',
    shareOfVoice: null,
    excludedDimensions: { share_of_voice: 'NO_COMPETITOR_SET' },
    degradationFlags: ['NO_AUTHORITY_DATA', 'NO_COMPETITOR_SET'],
    weights: {
      mention_rate: '40.00',
      citation_strength: '26.67',
      sentiment: '20.00',
      technical_foundation: '13.33',
    },
    competitors: [],
  },
  dimensions: helpscoutReport.dimensions.map((d) =>
    d.key === 'share_of_voice'
      ? { ...d, subscore: null, included: false, exclusionReason: 'NO_COMPETITOR_SET' }
      : {
          ...d,
          weight: {
            mention_rate: '40.00',
            citation_strength: '26.67',
            sentiment: '20.00',
            technical_foundation: '13.33',
          }[d.key]!,
        },
  ),
  competitorSet: null,
};

/** Technical Foundation never measured — our missing capability, not their fault. */
export const notYetMeasuredReport: Report = {
  ...helpscoutReport,
  score: {
    ...helpscoutReport.score!,
    technicalFoundation: null,
    excludedDimensions: { technical_foundation: 'NOT_YET_MEASURED' },
    degradationFlags: ['NO_AUTHORITY_DATA', 'TECHNICAL_FOUNDATION_NOT_MEASURED'],
  },
  dimensions: helpscoutReport.dimensions.map((d) =>
    d.key === 'technical_foundation'
      ? { ...d, subscore: null, included: false, exclusionReason: 'NOT_YET_MEASURED' }
      : d,
  ),
  audit: null,
};

/** The crawl could not read the site at all. Not a site with a bad foundation. */
export const failedAuditReport: Report = {
  ...helpscoutReport,
  audit: {
    ...helpscoutReport.audit!,
    status: 'failed',
    errorCode: 'FETCH_FAILED',
    technicalFoundation: null,
    passed: 0,
    warned: 0,
    failed: 1,
    notApplicable: 0,
    findings: [],
  },
};

/**
 * A set that has been corrected AND re-detected — Finding 3's actual case.
 *
 * `apply_override` clears the confidence, so an overridden set alone never
 * shows one. Re-detection then writes a fresh confidence (computed over the
 * candidates IT ranked) while the operator's rows survive, and the set carries
 * a real corroboration figure over a list that is only partly detection's.
 * That is the state in which publishing the figure unscoped overstates it.
 */
export const mixedCompetitorSetReport: Report = {
  ...helpscoutReport,
  competitorSet: {
    ...helpscoutReport.competitorSet!,
    detectionConfidence: '0.750',
    confidenceCovers: 4,
    competitors: helpscoutReport.competitorSet!.competitors.map((competitor, index) =>
      index === 0
        ? { ...competitor, isManualOverride: true, detectionSource: 'manual' }
        : competitor,
    ),
  },
};

/** Detection ran but the two signals barely agreed. */
export const weakSignalReport: Report = {
  ...helpscoutReport,
  competitorSet: {
    ...helpscoutReport.competitorSet!,
    status: 'weak_signal',
    detectionConfidence: '0.200',
  },
  score: {
    ...helpscoutReport.score!,
    degradationFlags: ['NO_AUTHORITY_DATA', 'WEAK_COMPETITOR_SET'],
  },
};

/** A scan that ran but was never scored at all. */
export const unscoredReport: Report = {
  ...helpscoutReport,
  score: null,
  dimensions: [],
};
