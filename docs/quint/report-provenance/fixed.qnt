// Source authentication precedes downloads; attempt recheck precedes rendering.
module report_provenance_fixed {
  var s: {
    phase: str, sourceEligible: bool, sameRevision: bool, attemptStable: bool,
    validated: bool, bothArtifacts: bool, reportsComplete: bool, readmeUpdated: bool
  }
  action init = s' = {
    phase: "idle", sourceEligible: false, sameRevision: false,
    attemptStable: true, validated: false, bothArtifacts: false,
    reportsComplete: false, readmeUpdated: false
  }
  action eligibleSource = all {
    s.phase == "idle",
    s' = { ...s, phase: "validate", sourceEligible: true, sameRevision: true }
  }
  action wrongSource = all {
    s.phase == "idle", s' = { ...s, phase: "validate", sameRevision: true }
  }
  action wrongRevision = all {
    s.phase == "idle", s' = { ...s, phase: "validate", sourceEligible: true }
  }
  action authenticateSource = all {
    s.phase == "validate", s.sourceEligible, s.sameRevision,
    s' = { ...s, phase: "fetching", validated: true }
  }
  action rejectSource = all {
    s.phase == "validate", not(s.sourceEligible and s.sameRevision),
    s' = { ...s, phase: "rejected" }
  }
  action artifactsDownloaded = all {
    s.phase == "fetching",
    s' = { ...s, phase: "recheck", bothArtifacts: true, reportsComplete: true }
  }
  action rerunDuringDownload = all {
    s.phase == "recheck", s.attemptStable, s' = { ...s, attemptStable: false }
  }
  action recheckAttempt = all {
    s.phase == "recheck", s.attemptStable, s' = { ...s, phase: "rendering" }
  }
  action rejectRerun = all {
    s.phase == "recheck", not(s.attemptStable), s' = { ...s, phase: "rejected" }
  }
  action updateStandingReadmePr = all {
    s.phase == "rendering", s.reportsComplete,
    s' = { ...s, phase: "complete", readmeUpdated: true }
  }
  action stay = s' = s
  action step = any {
    eligibleSource, wrongSource, wrongRevision, authenticateSource, rejectSource,
    artifactsDownloaded, rerunDuringDownload, recheckAttempt, rejectRerun,
    updateStandingReadmePr, all { Set("complete", "rejected").contains(s.phase), stay }
  }
  val readmeProvenanceHonest = not(s.readmeUpdated) or
    (s.validated and s.sourceEligible and s.sameRevision and s.attemptStable
      and s.bothArtifacts and s.reportsComplete)
  run eligibleSourceControlTest = init.then(eligibleSource).then(authenticateSource)
    .then(artifactsDownloaded).then(recheckAttempt).then(updateStandingReadmePr)
    .then(all { assert(s.readmeUpdated and readmeProvenanceHonest), stay })
  run wrongSourceRejectedTest = init.then(wrongSource).then(rejectSource)
    .then(all { assert(s.phase == "rejected" and not(s.readmeUpdated)), stay })
  run wrongRevisionRejectedTest = init.then(wrongRevision).then(rejectSource)
    .then(all { assert(s.phase == "rejected" and not(s.readmeUpdated)), stay })
  run rerunRejectedTest = init.then(eligibleSource).then(authenticateSource)
    .then(artifactsDownloaded).then(rerunDuringDownload).then(rejectRerun)
    .then(all { assert(s.phase == "rejected" and not(s.readmeUpdated)), stay })
}
