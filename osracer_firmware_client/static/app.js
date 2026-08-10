(() => {
  "use strict";

  const LANGUAGE_KEY = "osracer-firmware-client-language";
  const TEXT = {
    en: {
      pageTitle: "OSRacer Firmware Client",
      languageLabel: "Language",
      utilityEyebrow: "LOCAL DEVICE UTILITY",
      clientTitle: "Firmware Client",
      clientLede: "Official updates, custom App OTA, and controlled recovery in one local tool.",
      sessionCheck: "Session check",
      deviceEyebrow: "DEVICE",
      connectedTarget: "Connected target",
      inspectDevice: "Inspect device",
      notInspected: "Not inspected",
      officialPackage: "Official package",
      backupCapability: "Backup capability",
      battery: "Battery",
      currentSession: "CURRENT SESSION",
      ready: "Ready",
      noOperation: "No firmware operation is active.",
      operationsEyebrow: "OPERATIONS",
      selectPath: "Select one controlled path",
      singleOperation: "Only one operation can run at a time.",
      officialTab: "Official update",
      customTab: "Custom App",
      eraseTab: "Erase & restore",
      officialHeading: "Matched official package",
      officialDescription: "The device identity must map to exactly one embedded package. NVS is not erased.",
      reinstallLabel: "Reinstall if the target is already current",
      confirmationLabel: "Confirmation",
      typeUpdate: "Type UPDATE",
      runOfficial: "Run official update",
      customHeading: "Custom ESP32-S3 application",
      customDescription: "Only an ESP-IDF application image is accepted. Bootloader, partition table, and merged images are refused.",
      applicationImage: "Application image",
      typeFlashCustom: "Type FLASH CUSTOM",
      flashCustom: "Flash custom App",
      advancedOperation: "Advanced operation.",
      advancedWarning: "Raw NVS is backed up and verified first. All non-NVS persistent data is erased.",
      recoveryPackage: "Official recovery package",
      firstConfirmation: "First confirmation",
      typePrepare: "Type PREPARE {bundle}",
      prepareNvs: "Prepare and back up NVS",
      backupComplete: "Backup complete — review before erasing",
      lossAcknowledgement: "I understand all non-NVS data will be erased",
      secondConfirmation: "Second confirmation",
      eraseAndFlash: "ERASE AND FLASH {bundle}",
      executeErase: "Erase, restore official image, and restore NVS",
      activityEyebrow: "ACTIVITY",
      visibleLog: "Visible operation log",
      clientReady: "Client is ready.",
      operationActive: "Operation active",
      localSessionReady: "Local session ready",
      noAutomaticMatch: "No automatic match",
      unavailable: "Unavailable",
      completed: "Completed",
      operationCompleted: "Operation completed.",
      actionRequired: "Action required",
      reviewRequired: "Review required",
      sessionUnavailable: "Session unavailable",
      requestRejected: "Request rejected",
      requestFailed: "Request failed",
      selectApplicationError: "Select one application.bin file.",
      customUploadFailed: "Custom upload failed",
      prepareFirstError: "Prepare and verify a raw NVS backup first.",
      exactUrlError: "Open the exact local URL printed by the client.",
      bundle: "Bundle",
      rawNvsPath: "Raw NVS path",
      rawNvsSha: "Raw NVS SHA256",
      offsetSize: "Offset / size",
      capabilityManaged: "Managed configuration export",
      capabilityLegacy: "Legacy known-parameter backup",
      capabilityUnavailable: "Unavailable"
    },
    "zh-CN": {
      pageTitle: "OSRacer 固件更新客户端",
      languageLabel: "语言",
      utilityEyebrow: "本地设备工具",
      clientTitle: "固件更新客户端",
      clientLede: "在一个本地工具中完成官方更新、自定义 App OTA 与受控恢复。",
      sessionCheck: "会话检查",
      deviceEyebrow: "设备",
      connectedTarget: "已连接设备",
      inspectDevice: "检查设备",
      notInspected: "尚未检查",
      officialPackage: "官方固件",
      backupCapability: "备份能力",
      battery: "电池电压",
      currentSession: "当前会话",
      ready: "就绪",
      noOperation: "当前没有正在执行的固件操作。",
      operationsEyebrow: "操作",
      selectPath: "选择一种受控操作",
      singleOperation: "同一时间只能运行一项操作。",
      officialTab: "官方更新",
      customTab: "自定义 App",
      eraseTab: "擦除与恢复",
      officialHeading: "自动匹配官方固件",
      officialDescription: "设备身份必须唯一匹配一份内置官方固件；该操作不会擦除 NVS。",
      reinstallLabel: "目标版本已安装时仍重新安装",
      confirmationLabel: "确认口令",
      typeUpdate: "输入 UPDATE",
      runOfficial: "执行官方更新",
      customHeading: "自定义 ESP32-S3 应用程序",
      customDescription: "只接受 ESP-IDF application 镜像；bootloader、分区表和合并镜像会被拒绝。",
      applicationImage: "应用程序镜像",
      typeFlashCustom: "输入 FLASH CUSTOM",
      flashCustom: "烧写自定义 App",
      advancedOperation: "高级操作。",
      advancedWarning: "工具会先备份并验证原始 NVS；所有非 NVS 持久数据都会被擦除。",
      recoveryPackage: "官方恢复固件",
      firstConfirmation: "第一次确认",
      typePrepare: "输入 PREPARE {bundle}",
      prepareNvs: "准备并备份 NVS",
      backupComplete: "备份完成——擦除前请再次核对",
      lossAcknowledgement: "我已了解所有非 NVS 数据都会被擦除",
      secondConfirmation: "第二次确认",
      eraseAndFlash: "输入 ERASE AND FLASH {bundle}",
      executeErase: "擦除、恢复官方固件并还原 NVS",
      activityEyebrow: "活动",
      visibleLog: "操作日志（诊断信息保留英文）",
      clientReady: "客户端已就绪。",
      operationActive: "正在执行操作",
      localSessionReady: "本地会话已就绪",
      noAutomaticMatch: "没有自动匹配项",
      unavailable: "不可用",
      completed: "已完成",
      operationCompleted: "操作已完成。",
      actionRequired: "需要处理",
      reviewRequired: "请检查结果",
      sessionUnavailable: "会话不可用",
      requestRejected: "请求被拒绝",
      requestFailed: "请求失败",
      selectApplicationError: "请选择一个 application.bin 文件。",
      customUploadFailed: "自定义固件上传失败",
      prepareFirstError: "请先准备并验证原始 NVS 备份。",
      exactUrlError: "请打开客户端终端输出的完整本地地址。",
      bundle: "固件编号",
      rawNvsPath: "原始 NVS 路径",
      rawNvsSha: "原始 NVS SHA256",
      offsetSize: "偏移 / 大小",
      capabilityManaged: "受控配置导出",
      capabilityLegacy: "旧版已知参数备份",
      capabilityUnavailable: "不可用"
    }
  };

  const MESSAGE_ZH = {
    "Inspecting device": "正在检查设备",
    "Device inspection completed": "设备检查完成",
    "Embedded official firmware validated": "内置官方固件验证通过",
    "Logical parameter backup is unavailable; App-only OTA still preserves NVS": "无法导出逻辑参数；App OTA 仍会保留 NVS 分区",
    "Vehicle parameter backup stored": "车辆参数备份已保存",
    "Flashing application": "正在烧写应用程序",
    "Starting App-only OTA; NVS is not erased": "开始 App OTA；不会擦除 NVS",
    "Waiting for official firmware": "正在等待官方固件重新连接",
    "Selected official firmware is already installed": "选定的官方固件已经安装",
    "Official App update completed; NVS was preserved and parameters verified": "官方 App 更新完成；NVS 已保留，参数验证通过",
    "Validating custom ESP32-S3 application": "正在验证自定义 ESP32-S3 应用程序",
    "Custom application validated": "自定义应用程序验证通过",
    "Starting custom App-only OTA; NVS is not erased": "开始自定义 App OTA；不会擦除 NVS",
    "Custom App transfer completed; NVS was preserved; custom behavior is not certified": "自定义 App 传输完成；NVS 已保留，但自定义功能未经本工具认证",
    "Attempting logical vehicle parameter backup": "正在尝试备份车辆逻辑参数",
    "Logical parameter backup unavailable; raw NVS backup remains mandatory": "逻辑参数备份不可用；原始 NVS 备份仍是强制门槛",
    "Enter ESP32-S3 ROM download mode with BOOT/RESET if automatic reset does not connect": "如果自动复位无法连接，请使用 BOOT/RESET 进入 ESP32-S3 ROM 下载模式",
    "Reading raw NVS partition": "正在读取原始 NVS 分区",
    "Raw NVS is stored and verified; review paths before destructive confirmation": "原始 NVS 已保存并验证；执行擦除确认前请核对路径",
    "Reconnecting to ESP32-S3 ROM download mode": "正在重新连接 ESP32-S3 ROM 下载模式",
    "Erasing complete flash; non-NVS data will be lost": "正在擦除整个 Flash；非 NVS 数据将丢失",
    "Writing embedded official recovery image": "正在写入内置官方恢复镜像",
    "Restoring raw NVS partition": "正在恢复原始 NVS 分区",
    "Waiting for restored official firmware": "正在等待恢复后的官方固件",
    "Full recovery completed; raw NVS was restored and verified": "完整恢复完成；原始 NVS 已还原并验证"
  };

  const PHASE_ZH = {
    inspect: "设备检查",
    validate: "固件验证",
    backup: "参数备份",
    flash_app: "App 烧写",
    reconnect: "重新连接",
    result: "结果",
    rom: "ROM 模式",
    raw_nvs: "原始 NVS",
    confirm_erase: "擦除确认",
    erase: "整片擦除",
    recovery_flash: "恢复固件",
    nvs_restore: "NVS 恢复"
  };

  const STATUS_ZH = {
    started: "开始",
    completed: "完成",
    failed: "失败",
    unavailable: "不可用",
    progress: "进行中",
    waiting: "等待",
    ready: "就绪",
    success: "成功",
    skipped: "已跳过",
    stored: "已保存"
  };

  const token = window.location.hash.slice(1);
  const headers = {"X-Session-Token": token};
  let language = detectLanguage();
  let lastEventCount = -1;
  let currentPreparation = null;
  let currentDevice = null;
  let currentState = null;
  const $ = (id) => document.getElementById(id);

  function detectLanguage() {
    try {
      const stored = window.localStorage.getItem(LANGUAGE_KEY);
      if (stored === "en" || stored === "zh-CN") return stored;
    } catch (_error) {
      // Browser storage is optional; language detection still works without it.
    }
    const candidates = [...(navigator.languages || []), navigator.language || ""];
    return candidates.some((value) => value.toLowerCase().startsWith("zh")) ? "zh-CN" : "en";
  }

  function t(key, values = {}) {
    const template = TEXT[language][key] || TEXT.en[key] || key;
    return Object.entries(values).reduce(
      (result, [name, value]) => result.replaceAll(`{${name}}`, String(value)),
      template
    );
  }

  function localizeMessage(value) {
    if (language !== "zh-CN") return value;
    return MESSAGE_ZH[value] || value;
  }

  function translatePhase(value) {
    if (language === "zh-CN") return PHASE_ZH[value] || value;
    return value.replaceAll("_", " ");
  }

  function translateStatus(value) {
    if (language === "zh-CN") return STATUS_ZH[value] || value;
    return value;
  }

  function translateResultStatus(value) {
    if (language !== "zh-CN") return value || t("completed");
    return STATUS_ZH[value] || value || t("completed");
  }

  function updateConfirmationPlaceholders() {
    const bundle = $("erase-bundle").value;
    $("prepare-confirm").placeholder = t("typePrepare", {bundle});
    if (currentPreparation) {
      $("erase-confirm").placeholder = currentPreparation.required_confirmation;
    } else {
      $("erase-confirm").placeholder = t("eraseAndFlash", {bundle});
    }
  }

  function applyTranslations() {
    document.documentElement.lang = language;
    document.title = t("pageTitle");
    $("language-select").value = language;
    $("language-select").setAttribute("aria-label", t("languageLabel"));
    document.querySelectorAll("[data-i18n]").forEach((element) => {
      element.textContent = t(element.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
      const key = element.dataset.i18nPlaceholder;
      if (key !== "typePrepare" && key !== "eraseAndFlash") element.placeholder = t(key);
    });
    updateConfirmationPlaceholders();
  }

  function setLanguage(value, persist = true) {
    if (value !== "en" && value !== "zh-CN") return;
    language = value;
    if (persist) {
      try {
        window.localStorage.setItem(LANGUAGE_KEY, language);
      } catch (_error) {
        // A blocked localStorage must not block firmware operations.
      }
    }
    applyTranslations();
    lastEventCount = -1;
    if (currentDevice) renderDevice(currentDevice);
    if (currentState) renderState(currentState, true);
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {...options, headers: {...headers, ...(options.headers || {})}});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || `${t("requestFailed")} (${response.status})`);
    return value;
  }

  function postJson(path, value) {
    return request(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(value)});
  }

  function setBusy(busy) {
    document.querySelectorAll("button, input, select").forEach((element) => {
      if (!["erase-confirm", "loss-check", "language-select"].includes(element.id)) element.disabled = busy;
    });
    $("connection-pill").textContent = busy ? t("operationActive") : t("localSessionReady");
    $("connection-pill").className = `pill ${busy ? "neutral" : "good"}`;
  }

  function renderDevice(value) {
    if (!value || !value.project_version) return;
    currentDevice = value;
    const capabilities = {
      managed: t("capabilityManaged"),
      legacy: t("capabilityLegacy"),
      unavailable: t("capabilityUnavailable")
    };
    $("device-details").innerHTML = `
      <div><dt>ProjectVer</dt><dd>${escapeHtml(value.project_version)}</dd></div>
      <div><dt>${escapeHtml(t("officialPackage"))}</dt><dd>${escapeHtml(value.official_bundle_id || t("noAutomaticMatch"))}</dd></div>
      <div><dt>${escapeHtml(t("backupCapability"))}</dt><dd>${escapeHtml(capabilities[value.backup_capability] || t("unavailable"))}</dd></div>
      <div><dt>${escapeHtml(t("battery"))}</dt><dd>${Number(value.battery_voltage).toFixed(2)} V</dd></div>`;
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, (character) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[character]));
  }

  function renderEvents(events, force = false) {
    if (!force && events.length === lastEventCount) return;
    lastEventCount = events.length;
    $("activity-log").innerHTML = events.length ? events.slice(-80).map((event) => `
      <li><span class="phase">${escapeHtml(translatePhase(event.phase))}</span><span class="status">${escapeHtml(translateStatus(event.status))}</span><span>${escapeHtml(localizeMessage(event.message))}</span></li>`).join("") : `<li>${escapeHtml(t("clientReady"))}</li>`;
    const latest = events[events.length - 1];
    if (latest) {
      $("session-title").textContent = translatePhase(latest.phase);
      $("session-message").textContent = localizeMessage(latest.message);
      if (typeof latest.progress === "number") {
        const percent = Math.round(latest.progress * 100);
        $("progress-bar").style.width = `${percent}%`;
        $("progress-label").textContent = `${percent}%`;
      }
    }
  }

  function renderPreparation(preparation) {
    currentPreparation = preparation;
    if (!preparation) {
      $("erase-ready").classList.add("hidden");
      updateConfirmationPlaceholders();
      return;
    }
    const raw = preparation.raw_nvs_backup;
    $("erase-details").innerHTML = `
      <div><dt>${escapeHtml(t("bundle"))}</dt><dd>${escapeHtml(preparation.bundle_id)}</dd></div>
      <div><dt>${escapeHtml(t("rawNvsPath"))}</dt><dd>${escapeHtml(raw.path)}</dd></div>
      <div><dt>${escapeHtml(t("rawNvsSha"))}</dt><dd>${escapeHtml(raw.sha256)}</dd></div>
      <div><dt>${escapeHtml(t("offsetSize"))}</dt><dd>0x${raw.offset.toString(16)} / 0x${raw.size.toString(16)}</dd></div>`;
    $("erase-confirm").placeholder = preparation.required_confirmation;
    $("erase-ready").classList.remove("hidden");
  }

  function renderState(state, force = false) {
    currentState = state;
    setBusy(state.busy);
    renderEvents(state.events || [], force);
    renderPreparation(state.erase_preparation);
    if (state.result) {
      if (state.operation === "inspect") renderDevice(state.result);
      $("session-title").textContent = translateResultStatus(state.result.status);
      $("session-message").textContent = localizeMessage(state.result.message || t("operationCompleted"));
    }
    if (state.error) {
      $("session-title").textContent = t("actionRequired");
      $("session-message").textContent = state.error.message;
      $("connection-pill").textContent = t("reviewRequired");
      $("connection-pill").className = "pill bad";
    }
  }

  async function poll() {
    try {
      renderState(await request("/api/state"));
    } catch (error) {
      $("connection-pill").textContent = t("sessionUnavailable");
      $("connection-pill").className = "pill bad";
      $("session-message").textContent = error.message;
    } finally {
      window.setTimeout(poll, 500);
    }
  }

  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item === tab));
    document.querySelectorAll(".tab-content").forEach((item) => item.classList.toggle("active", item.id === `tab-${tab.dataset.tab}`));
  }));

  $("language-select").addEventListener("change", (event) => setLanguage(event.target.value));
  $("erase-bundle").addEventListener("change", updateConfirmationPlaceholders);
  $("inspect-button").addEventListener("click", () => request("/api/inspect", {method: "POST"}).catch(showError));
  $("official-button").addEventListener("click", () => postJson("/api/official", {
    confirmation: $("official-confirm").value,
    reinstall: $("reinstall").checked
  }).catch(showError));
  $("custom-button").addEventListener("click", async () => {
    const file = $("custom-file").files[0];
    if (!file) return showError(new Error(t("selectApplicationError")));
    try {
      const response = await fetch("/api/custom", {method: "POST", headers: {...headers, "Content-Type": "application/octet-stream", "X-Filename": file.name, "X-Confirmation": $("custom-confirm").value}, body: file});
      const value = await response.json();
      if (!response.ok) throw new Error(value.error || t("customUploadFailed"));
    } catch (error) {
      showError(error);
    }
  });
  $("prepare-button").addEventListener("click", () => postJson("/api/erase/prepare", {
    bundle_id: $("erase-bundle").value,
    confirmation: $("prepare-confirm").value
  }).catch(showError));
  $("erase-button").addEventListener("click", () => {
    if (!currentPreparation) return showError(new Error(t("prepareFirstError")));
    return postJson("/api/erase/execute", {
      preparation_id: currentPreparation.preparation_id,
      acknowledge_non_nvs_loss: $("loss-check").checked,
      confirmation: $("erase-confirm").value
    }).catch(showError);
  });

  function showError(error) {
    $("session-title").textContent = t("requestRejected");
    $("session-message").textContent = error.message;
  }

  applyTranslations();
  if (!token) showError(new Error(t("exactUrlError")));
  poll();
})();
