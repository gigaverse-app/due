# Changelog

## [0.7.1](https://github.com/gigaverse-app/due/compare/v0.7.0...v0.7.1) (2026-10-21)


### Documentation

* use release versions for editable source consumers ([#59](https://github.com/gigaverse-app/due/issues/59)) ([1e4e566](https://github.com/gigaverse-app/due/commit/1e4e566b7dde70611e04efa0d5215a9a2d9c4c6e))

## [0.7.0](https://github.com/gigaverse-app/due/compare/v0.6.0...v0.7.0) (2026-10-21)


### Features

* allow inline callers to wake work without a worker notification ([#51](https://github.com/gigaverse-app/due/issues/51)) ([c13a3ca](https://github.com/gigaverse-app/due/commit/c13a3cacb6ab6b2f5687156ed4088023768dd9ce))
* share checkpointed projection recovery and stopped retries ([#57](https://github.com/gigaverse-app/due/issues/57)) ([018c4e7](https://github.com/gigaverse-app/due/commit/018c4e772b791b9b52801c2adaffd6a5588c6143))

## [0.6.0](https://github.com/gigaverse-app/due/compare/v0.5.0...v0.6.0) (2026-10-21)


### Features

* use shared recovery enforcement with simpler paged adoption ([#55](https://github.com/gigaverse-app/due/issues/55)) ([3a4753c](https://github.com/gigaverse-app/due/commit/3a4753cae5338be24ff7588257ccb638b801c9fc))


### Bug fixes

* bind recovery proofs to their targets and isolate step retries ([#56](https://github.com/gigaverse-app/due/issues/56)) ([336b430](https://github.com/gigaverse-app/due/commit/336b43024be47839d612e8eebc90a43567a483ba))
* enforce coverage for executable recovery steps ([0c59476](https://github.com/gigaverse-app/due/commit/0c59476c4a73968c4ba5601c74c0e1d7d93b9821))

## [0.5.0](https://github.com/gigaverse-app/due/compare/v0.4.0...v0.5.0) (2026-10-13)


### Features

* a projection retires older-revision repair once its provider target is gone ([#49](https://github.com/gigaverse-app/due/issues/49)) ([ddcb506](https://github.com/gigaverse-app/due/commit/ddcb5065a1c15a5caa54bf1a0c42a0d4a38ef24b))


### Bug fixes

* a store that redacts its receipts still passes late-evidence settlement ([#48](https://github.com/gigaverse-app/due/issues/48)) ([07456e8](https://github.com/gigaverse-app/due/commit/07456e8016271542fc47f09802cd0e248476b84b))

## [0.4.0](https://github.com/gigaverse-app/due/compare/v0.3.1...v0.4.0) (2026-10-12)


### Features

* a retired provider target settles repair without inventing a receipt ([#46](https://github.com/gigaverse-app/due/issues/46)) ([43abdd4](https://github.com/gigaverse-app/due/commit/43abdd4cf94a9bd25266646ed19fd9fd34ce600b))

## [0.3.1](https://github.com/gigaverse-app/due/compare/v0.3.0...v0.3.1) (2026-10-12)


### Bug fixes

* checkpointed replays and translated held-claim reports pass their histories ([#44](https://github.com/gigaverse-app/due/issues/44)) ([3876be2](https://github.com/gigaverse-app/due/commit/3876be2e6f7c76f672bec72b0f243850adf4b53b))

## [0.3.0](https://github.com/gigaverse-app/due/compare/v0.2.0...v0.3.0) (2026-10-12)


### Features

* crash-ambiguity proofs settle through the application's evidence entry point ([#38](https://github.com/gigaverse-app/due/issues/38)) ([b07b879](https://github.com/gigaverse-app/due/commit/b07b879e2113eca939ac197d7af96f70c9306f3b))
* discovery admissibility perturbs the related rows a registration names ([#40](https://github.com/gigaverse-app/due/issues/40)) ([d87ca84](https://github.com/gigaverse-app/due/commit/d87ca845a49e63ed7c96a02796ebb05982ec3312))
* generated caller and wake-command histories through the adopter's real entry points ([#39](https://github.com/gigaverse-app/due/issues/39)) ([0be6f9b](https://github.com/gigaverse-app/due/commit/0be6f9bcb186ddaa0ddb1cbb7cf12548c2e3b7d4))
* replay-safe work tables prove a lost acknowledgement replays to one settled outcome ([#37](https://github.com/gigaverse-app/due/issues/37)) ([c0d1be0](https://github.com/gigaverse-app/due/commit/c0d1be08d471a7a54dc1d15d109a874529d6c502))
* simplify transactional continuations and measure workflow resource cost ([#36](https://github.com/gigaverse-app/due/issues/36)) ([a3024eb](https://github.com/gigaverse-app/due/commit/a3024ebafbbf816c9d2a0b9fbe1a1bf92a2d0028))


### Performance

* reduce worker CPU and connection-loss recovery overhead ([#41](https://github.com/gigaverse-app/due/issues/41)) ([7a8be87](https://github.com/gigaverse-app/due/commit/7a8be874b5558905d7756e7f2e06f0a91ad724d5))

## [0.2.0](https://github.com/gigaverse-app/due/compare/v0.1.0...v0.2.0) (2026-10-12)


### Features

* accelerate durable continuations with transient deadline wakeups ([#34](https://github.com/gigaverse-app/due/issues/34)) ([56a04fa](https://github.com/gigaverse-app/due/commit/56a04fa5c242e9c9822e50d6f91495d3932240f2))
* demonstrate durable widget checkout with DBOS comparison ([#33](https://github.com/gigaverse-app/due/issues/33)) ([9e51063](https://github.com/gigaverse-app/due/commit/9e51063e762884f98d42514763e6b11f381f9791))

## 0.1.0 (2026-10-12)


### Features

* add a runnable books example with recovery proofs ([57b94db](https://github.com/gigaverse-app/due/commit/57b94dbc98fda1d95745d63d81b7408cdb940ee3))
* add books indexing demo and recoverable handoff proofs ([dd941a3](https://github.com/gigaverse-app/due/commit/dd941a317638938e424e823e07a5bee74841ee37))
* add comparative recovery demos and setup diagnostics ([20adee4](https://github.com/gigaverse-app/due/commit/20adee48526f247690dc7e088434e68f3a09f6ed))
* add executable Celery comparison and pin Books port reference ([3d62279](https://github.com/gigaverse-app/due/commit/3d6227905e20554b851ed8bdaab09b9fffdaf810))
* add executable migration demo comparisons ([5e2aba9](https://github.com/gigaverse-app/due/commit/5e2aba9cf79d9481b4327e099b2fbb11634edf1c))
* add scheduled publication with deadline hints and submission keys ([5795f0c](https://github.com/gigaverse-app/due/commit/5795f0c5db8c49618ddb806ec189d64e17a19140))
* complete backend harness adapters and proof controls ([141af43](https://github.com/gigaverse-app/due/commit/141af432f6cd1a2203c9d9ead0caa518274edf47))
* complete discovery proofs and provider seam bindings ([87e7b12](https://github.com/gigaverse-app/due/commit/87e7b120396796c0733e411fdf503797c3a90d3a))
* complete work-table tooling and contributor workflow ([aa5fbfc](https://github.com/gigaverse-app/due/commit/aa5fbfcd8aac919696430e94915a2643f78fbb14))
* demonstrate and compare application recovery guarantees ([0a19778](https://github.com/gigaverse-app/due/commit/0a19778975824c735a784b60ad204468b388a39b))
* demonstrate scheduled publication with cancellation and recovery proofs ([0a3e38a](https://github.com/gigaverse-app/due/commit/0a3e38a91f93b72e89a8aa00d174fe52852a65a6))
* every work table can declare its provider outcome matrix ([#27](https://github.com/gigaverse-app/due/issues/27)) ([5ee2b7b](https://github.com/gigaverse-app/due/commit/5ee2b7b1d3d6d9f49f4bf21cbe965e3f603b17a2))
* extract Django work-table execution and recovery into Due ([a1e30be](https://github.com/gigaverse-app/due/commit/a1e30bec92dd03fd382ded0a6ad799b87c320914))
* finish Beat and interleaving harness adapters ([ed3de62](https://github.com/gigaverse-app/due/commit/ed3de62ea03b8755cd53c913652b75a5d57eb9f9))
* integrate adopter proofs and recover lifecycle tooling ([1204181](https://github.com/gigaverse-app/due/commit/1204181e06018dc5778bf146444824595ed6f5ec))
* port application-bound work-table conformance harness ([65e0bcf](https://github.com/gigaverse-app/due/commit/65e0bcfe426c9e663aec4ae5c71398965dde3269))
* port application-bound work-table conformance harness ([d682f93](https://github.com/gigaverse-app/due/commit/d682f93cd8c1862f0f5b49e2e6cca9957db2f742))
* preserve application fatal recovery errors and bind host proofs ([96d7ab2](https://github.com/gigaverse-app/due/commit/96d7ab2427076a810ff687766ac88e725c579064))
* preserve application recovery failures and document proof integration ([ab8ff31](https://github.com/gigaverse-app/due/commit/ab8ff31104fa2e42a997a3637e04982665789a13))
* preserve backend handoff recovery and harness mutation controls ([43b88af](https://github.com/gigaverse-app/due/commit/43b88af5f92ab324f9e49c576eb3599c8bb53e32))
* preserve deployment contracts and stronger work-table bindings ([a241d70](https://github.com/gigaverse-app/due/commit/a241d70e5e3110ee3c78e389d415b76e838f33ed))
* preserve discovery completeness and failed recovery evidence ([65d4a86](https://github.com/gigaverse-app/due/commit/65d4a8688fc78b4ec7d83b1f66c63ce9b9fb9c4f))
* prove application outcomes and publish immutable files without replay writes ([92bfacd](https://github.com/gigaverse-app/due/commit/92bfacdb2f23b1b1959e40ae02364fef9103edee))
* prove books input stability and require demo replay outcomes ([b19425e](https://github.com/gigaverse-app/due/commit/b19425eeda04c2a090e300faab733269caafcd53))
* prove immutable input claims against admission and recovery ([ac847ca](https://github.com/gigaverse-app/due/commit/ac847ca05ef3bf7afbe2f3c54c684e5014ce7de6))
* publish django-due through manually approved releases ([#26](https://github.com/gigaverse-app/due/issues/26)) ([bdcb1e6](https://github.com/gigaverse-app/due/commit/bdcb1e657861412bf85b8ed56dec393b4454b230))
* restore work-table operator tooling and harden contributor checks ([2766c33](https://github.com/gigaverse-app/due/commit/2766c33f2456886bba0f9b6c29b17018dcd2178f))
* share typed provider probes and complete Celery setup ([6765a0a](https://github.com/gigaverse-app/due/commit/6765a0ab7f079d5fe2543e26bb9915848f7a43b2))
* simplify runtime and provider bindings ([d1ebe81](https://github.com/gigaverse-app/due/commit/d1ebe81bb815ccdd96070c84d17fb8db5718143b))
* unbounded work tables claim profile J through the persistent-failure proof ([d6c8a4b](https://github.com/gigaverse-app/due/commit/d6c8a4b9ddfb3215560b938c3dcd49dd7759a088))
* unbounded work tables claim profile J through the persistent-failure proof ([c642482](https://github.com/gigaverse-app/due/commit/c642482a7853dd130719382f3cb16f01c21b84ed))
* upstream native lifecycle and cutover integration improvements ([93ca10c](https://github.com/gigaverse-app/due/commit/93ca10cb8ae11a93e1e13b21f78e7a9a0e64ce4e))
* verify immutable input claims through production admission ([533530e](https://github.com/gigaverse-app/due/commit/533530e7377d3d32e749caaef387a38a6dcf919f))
* wake near deadlines without SQL hints and deduplicate submissions ([eaa043f](https://github.com/gigaverse-app/due/commit/eaa043f5140b531f044212065bcc596c59d5bb1d))


### Bug fixes

* consume queued recovery in the comparison harness ([9a3003e](https://github.com/gigaverse-app/due/commit/9a3003e07389f1cdbd8b53bea1cebca84f100ad7))
* failing_external_call counts every provider entry ([f4c8202](https://github.com/gigaverse-app/due/commit/f4c820257a7211ca53d1ce2e65f4812dcb8ddf05))
* initialize the example Celery app in Django processes ([5baa593](https://github.com/gigaverse-app/due/commit/5baa5938bf8c731bfe808ba8fc6ba1dbd5721c95))
* keep gate recovery on the harness-owned broker ([286e694](https://github.com/gigaverse-app/due/commit/286e6943afbc1efce1fb1e35c49fdf17f3deb400))
* preserve delivery verdicts and consolidate recovery ownership ([810c0c0](https://github.com/gigaverse-app/due/commit/810c0c0e9591e04aa853f69e5d52f512e47fd586))
* preserve proof failures through adopter cleanup ([b862931](https://github.com/gigaverse-app/due/commit/b8629317003551c78c518ba899a721752d152497))
* register deadline recovery proof and validate final harness candidate ([424a446](https://github.com/gigaverse-app/due/commit/424a44635c2ffacf01e499d4d6c710fdc5f6b31f))
* retain adopter environment throughout crash histories ([f7b8713](https://github.com/gigaverse-app/due/commit/f7b871301d6ca376f39d43ae59449e0da343b9e1))


### Performance

* publish immutable revisions cheaply with application outcome proofs ([f68e3e4](https://github.com/gigaverse-app/due/commit/f68e3e41098134c33fc0aab3fe351885c9157e30))
* remove per-publication staging directories without weakening recovery ([4ce9e34](https://github.com/gigaverse-app/due/commit/4ce9e3495068a3496e38f57587a23aa251f7f7bc))


### Documentation

* add coming-from guides for Python background-work libraries ([a65ee04](https://github.com/gigaverse-app/due/commit/a65ee0471ab51b28a4cde5897ff928f3fee5e806))
* add focused coming-from guides for Python work libraries ([054e65c](https://github.com/gigaverse-app/due/commit/054e65c2a86101ca9ba7fa7c440e957788660222))
* audit backend migration readiness ([#6](https://github.com/gigaverse-app/due/issues/6)) ([07e4b8b](https://github.com/gigaverse-app/due/commit/07e4b8be06563130475679a6c81484aa3632a5c4))
* compare Python queues and document migration and shim boundaries ([0893cd5](https://github.com/gigaverse-app/due/commit/0893cd5797961c731dc12eeb79737f6756599e4d))
* establish in-repository example catalog ([599c43f](https://github.com/gigaverse-app/due/commit/599c43f637d6eb97e6513a2ee9c169a2c719c1e9))
* explain Due strengths, tradeoffs and evidence boundaries ([4e635bc](https://github.com/gigaverse-app/due/commit/4e635bcfd1dde336ff4a5ddd9bd5b5f3f5c8c5ae))
* explain Due strengths, tradeoffs and evidence boundaries ([2d8e2dd](https://github.com/gigaverse-app/due/commit/2d8e2dd7a7c0aca2eeacc1ee78fd87a3561854a8))
* explain Temporal parity gaps and scheduled demo tradeoffs ([4998a18](https://github.com/gigaverse-app/due/commit/4998a18de69f6946728796d413a60964a6c70efa))
* format migration examples and check Markdown before commit ([fc6978d](https://github.com/gigaverse-app/due/commit/fc6978dfd751daf892c0edae6e96ce8573f1d088))
* guide conformance adoption and separate public authoring APIs ([6e0c061](https://github.com/gigaverse-app/due/commit/6e0c061c3a339e96803ffa3d81fb30817f5a6bcb))
* introduce Due durable obligations library ([3ac929f](https://github.com/gigaverse-app/due/commit/3ac929f175d25de951125a219d41c20d1b3b00b4))
* keep concurrent runtime adoption audit separate ([118486f](https://github.com/gigaverse-app/due/commit/118486f7a430bbb56864342870522dffd302efd8))
* link deadline wakeup evidence and completed recovery checks ([3752750](https://github.com/gigaverse-app/due/commit/37527507b6c4a79a30ad635ae390bfe11a6039dd))
* pin the companion harness audit reference ([2f48257](https://github.com/gigaverse-app/due/commit/2f48257ee403f91009a29c6df015c9f89d758fd7))
* plan application outcome and provider replay proofs ([b06ba90](https://github.com/gigaverse-app/due/commit/b06ba90322f9bc2cee3af19c5a1a7f5ebbef6464))
* plan audit-driven integration and recovery guides ([62f8688](https://github.com/gigaverse-app/due/commit/62f868824649409407963ea4427396d5a9da7087))
* plan backend replacement contract parity ([086b1be](https://github.com/gigaverse-app/due/commit/086b1bead3aa5c09524f7db732f71cea6b94f77a))
* plan checked immutable-input parity ([10e9eb0](https://github.com/gigaverse-app/due/commit/10e9eb031645ee38b68dc349a1333ce4bcce852a))
* plan comparative recovery and richer application demos ([a9cde2a](https://github.com/gigaverse-app/due/commit/a9cde2a9f3263d51ceaf19f82553426438c2afd0))
* plan current backend parity controls ([f8bb0e7](https://github.com/gigaverse-app/due/commit/f8bb0e762e9f1175ddecb1d925c4d9a5c4baf98f))
* plan executable books convergence guarantees ([9bc29d7](https://github.com/gigaverse-app/due/commit/9bc29d7e9156747eaf35daafdfe8f2cef1184382))
* plan real adopter integration and third source audit ([24428dc](https://github.com/gigaverse-app/due/commit/24428dc029b04e15bff70567f8545587afb2a34d))
* plan runtime and provider binding improvements ([3d79a87](https://github.com/gigaverse-app/due/commit/3d79a8789ba1d0a68bbc853950fe5a0b087aca2a))
* plan scheduled publication demo and recovery proofs ([8fdd8a6](https://github.com/gigaverse-app/due/commit/8fdd8a64822b70a521b5b83f728daae0039731fe))
* plan work-table parity and contributor workflow ([3d581ef](https://github.com/gigaverse-app/due/commit/3d581ef78a0886a89e0690c4102ded8739ac2b02))
* record audit port verification and migration limits ([a6d9d1a](https://github.com/gigaverse-app/due/commit/a6d9d1aeab4646be4d210f5aa333d50c88688d46))
* record binding integrity and integration evidence ([bf95077](https://github.com/gigaverse-app/due/commit/bf95077a634f70d0e94e000e64930e7537d8f5ed))
* record comparison and recovery validation evidence ([65f7cdc](https://github.com/gigaverse-app/due/commit/65f7cdcedcb5997f056b586293f9bd98f9c755bb))
* record complete serial consumer validation ([92433ad](https://github.com/gigaverse-app/due/commit/92433adfb9934f8c6d758434edbc7571cb14960d))
* record completed package and backend parity checks ([d779b1c](https://github.com/gigaverse-app/due/commit/d779b1cc8d838865883a6915c3713dcf7fbe55e4))
* record scheduled publication recovery evidence ([ff87b07](https://github.com/gigaverse-app/due/commit/ff87b074a9e44391189a5246c356a9321f2139bb))
* record standalone verification and supported Django baseline ([22153f1](https://github.com/gigaverse-app/due/commit/22153f1281c7fe87b4a64f688bc246994c2f8c4f))
* record the publication demo Procrastinate provenance ([a896b12](https://github.com/gigaverse-app/due/commit/a896b128102aaf01dbe0df497259e893f188c9fc))
* record work-table parity verification ([f5b4464](https://github.com/gigaverse-app/due/commit/f5b446407d3fba8e428725067d722069e89fd2c0))
* show concrete APIs and code changes in migration guides ([205bde7](https://github.com/gigaverse-app/due/commit/205bde743c989bd1721cbd543cdef4dd9a453f4e))
* start competitor migration audit ([7d625a8](https://github.com/gigaverse-app/due/commit/7d625a82af8477abf6f0428ae9373dde1afb1e8d))
