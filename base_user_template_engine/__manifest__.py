# -*- coding: utf-8 -*-
{
    "name": "User Template Engine",
    "summary": "Multi-template user provisioning: inheritance, tags, merge/override "
               "policies and bulk application of access rights and preferences.",
    "version": "17.0.1.0.0",
    "category": "Administration",
    "author": "Ibrahim Yahia",
    "website": "https://www.linkedin.com/in/ibrahim-yahia",
    "license": "LGPL-3",
    "depends": ["base", "web"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/res_users_template_views.xml",
        "views/res_users_tag_views.xml",
        "views/res_users_template_log_views.xml",
        "views/res_users_views.xml",
        "wizard/res_users_template_apply_wizard_views.xml",
        "views/menus.xml",
    ],
    "demo": [
        "demo/user_template_demo.xml",
    ],
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": False,
}
