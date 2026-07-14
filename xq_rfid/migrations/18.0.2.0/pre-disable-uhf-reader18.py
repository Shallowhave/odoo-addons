def migrate(cr, installed_version):
    del installed_version
    cr.execute("SELECT to_regclass('rfid_device_config')")
    if not cr.fetchone()[0]:
        return
    cr.execute("""
        SELECT column_name
          FROM information_schema.columns
         WHERE table_name = 'rfid_device_config'
           AND column_name IN ('active', 'device_type', 'connection_status', 'error_message')
    """)
    columns = {row[0] for row in cr.fetchall()}
    if {'active', 'device_type'} - columns:
        return
    assignments = ["active = FALSE", "device_type = %s"]
    params = ["legacy_disabled"]
    if 'connection_status' in columns:
        assignments.append("connection_status = %s")
        params.append("error")
    if 'error_message' in columns:
        assignments.append("error_message = %s")
        params.append("旧 UHFReader18 配置已停用；必须按 SI120X1 实机接口重新配置并验证。")
    params.append("uhf_reader18")
    cr.execute(
        f"UPDATE rfid_device_config SET {', '.join(assignments)} WHERE device_type = %s",
        params,
    )
