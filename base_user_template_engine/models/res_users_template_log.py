# -*- coding: utf-8 -*-
from odoo import fields, models


class ResUsersTemplateLog(models.Model):
    _name = "res.users.template.log"
    _description = "User Template Application Log"
    _order = "id desc"
    _rec_name = "user_id"

    user_id = fields.Many2one("res.users", required=True, ondelete="cascade", index=True)
    template_ids = fields.Many2many(
        "res.users.template", "res_users_template_log_rel", "log_id", "template_id",
        string="Templates")
    trigger = fields.Selection([
        ("manual", "Manual"),
        ("wizard", "Bulk Wizard"),
        ("create", "User Creation"),
        ("tag_change", "Tag Change"),
        ("cron", "Scheduled Sync"),
    ], required=True, default="manual")
    changes = fields.Text(readonly=True)
