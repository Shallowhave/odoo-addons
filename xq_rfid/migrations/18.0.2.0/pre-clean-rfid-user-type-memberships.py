def migrate(cr, version):
    del version
    cr.execute(
        """
        DELETE FROM res_groups_users_rel AS rfid_membership
         USING ir_model_data AS rfid_group,
               res_groups_users_rel AS user_type_membership,
               ir_model_data AS user_type_group
         WHERE rfid_group.module = 'xq_rfid'
           AND rfid_group.model = 'res.groups'
           AND rfid_group.name = ANY(%s)
           AND rfid_membership.gid = rfid_group.res_id
           AND rfid_membership.uid = user_type_membership.uid
           AND user_type_group.module = 'base'
           AND user_type_group.model = 'res.groups'
           AND user_type_group.name = ANY(%s)
           AND user_type_membership.gid = user_type_group.res_id
        """,
        (
            ["group_rfid_user", "group_rfid_manager"],
            ["group_portal", "group_public"],
        ),
    )
