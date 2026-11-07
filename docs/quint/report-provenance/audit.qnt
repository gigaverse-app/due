// Due's README report workflow. A manual dispatch resolves RUN_ID straight from
// inputs.run-id, downloads two artifact names, and renders; it does not query the
// source run for branch/event/conclusion/head SHA or bind both reports to it.
// Source: due/.github/workflows/obligation-report.yml; action contract:
// pytest-obligation/report/action.yml and reporting/lanes.py.
module manual_report_dispatch {
  var s: {
    phase: str, trigger: str, sourceEligible: bool, sameRevision: bool,
    bothArtifacts: bool, outcomeKnown: bool, reportsComplete: bool,
    changed: bool, readmeUpdated: bool
  }

  action init = s' = {
    phase: "idle", trigger: "none", sourceEligible: false,
    sameRevision: false, bothArtifacts: false, outcomeKnown: false,
    reportsComplete: false, changed: false, readmeUpdated: false
  }

  // Normal weekly trigger is filtered to a scheduled CI run on main.
  action scheduledCiCompleted = all {
    s.phase == "idle",
    s' = { ...s, phase: "fetching", trigger: "schedule",
      sourceEligible: true, sameRevision: true }
  }

  // The workflow_dispatch input has descriptive text saying "complete run on
  // main", but the workflow performs no API lookup to enforce that contract.
  action manualRunIdSelected = all {
    s.phase == "idle",
    s' = { ...s, phase: "fetching", trigger: "dispatch",
      sourceEligible: false, sameRevision: false }
  }

  // Both download-artifact steps are keyed only by the supplied run ID and
  // artifact names. The report action checks report count/schema, not provenance.
  action artifactsDownloaded = all {
    s.phase == "fetching",
    s' = { ...s, phase: "rendering", bothArtifacts: true,
      outcomeKnown: true, reportsComplete: true }
  }

  action evidenceRejected = all {
    s.phase == "fetching",
    s' = { ...s, phase: "rejected", bothArtifacts: false,
      reportsComplete: false }
  }

  action unchanged = all {
    s.phase == "rendering", s.reportsComplete,
    s' = { ...s, phase: "complete", changed: false }
  }

  action updateStandingReadmePr = all {
    s.phase == "rendering", s.reportsComplete,
    s' = { ...s, phase: "complete", changed: true, readmeUpdated: true }
  }

  action step = any { scheduledCiCompleted, manualRunIdSelected,
    artifactsDownloaded, evidenceRejected, unchanged, updateStandingReadmePr }

  // README text labels its source "weekly complete CI run on main". The
  // advertised provenance is stronger than what manual dispatch currently checks.
  val readmeProvenanceHonest = not(s.readmeUpdated) or
    (s.sourceEligible and s.sameRevision and s.bothArtifacts and
      s.outcomeKnown and s.reportsComplete)

  run scheduledSourcePositiveTest = init.then(scheduledCiCompleted)
    .then(artifactsDownloaded).then(updateStandingReadmePr)
    .then(all { assert(readmeProvenanceHonest), s' = s })

  // Expected counterexample: artifact shape/completeness is not source-run
  // eligibility. A manually selected PR or partial run can enter the README PR.
  run wrongManualRunCounterexampleTest = init.then(manualRunIdSelected)
    .then(artifactsDownloaded).then(updateStandingReadmePr)
    .then(all { assert(not(readmeProvenanceHonest)), s' = s })
}
