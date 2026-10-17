from pathlib import Path

REPORT = Path("experiments/oepl-s3-c6/m4-acceptance-report.md")


def test_m4_report_has_gated_statuses_and_no_rf_pass_claim() -> None:
    text = REPORT.read_text(encoding="utf-8")
    assert "M4_SOFTWARE_READY=READY" in text
    assert "FIRST_TAG_RF_STATUS=IMAGE_UPDATE_ACCEPTED_DISPLAY_UNVERIFIED" in text
    assert "FIRST_TAG_AVAILABLE=YES" in text
    assert "record_count=1" in text
    assert "updatecount=0" in text
    assert "HTTP=200" in text
    assert "显示更新未验证" in text
    assert "REAL_RF_PASS" in text
    assert "没有显示完成和重试证据" in text
    assert "serial" not in text.lower() or "串口" in text


def test_m4_report_requires_exact_tag_identity_before_rf() -> None:
    text = REPORT.read_text(encoding="utf-8")
    assert "具体型号、固件" in text
    assert "汉朔二手 2.66 英寸" in text
    assert "UNKNOWN" in text
    assert "用户明确授权一次受控 RF 操作" in text


def test_m4_report_is_not_bound_to_external_checkout() -> None:
    text = REPORT.read_text(encoding="utf-8")
    assert "/home/parallels/OpenEPaperLink-git" not in text
    assert "ESP32_AP-Flasher/src/web.cpp" in text
