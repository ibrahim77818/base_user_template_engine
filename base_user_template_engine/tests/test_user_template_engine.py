# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.fields import Command
from odoo.tests import Form, TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "user_template_engine")
class TestUserTemplateEngine(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env["res.users.template"]
        cls.Tag = cls.env["res.users.tag"]
        # Isolate from demo data: disable every pre-existing template.
        cls.Template.search([]).write({"active": False})

        cls.company_a = cls.env.ref("base.main_company")
        cls.company_b = cls.env["res.company"].create({"name": "UTE Company B"})

        cls.g_user = cls.env.ref("base.group_user")
        cls.g_system = cls.env.ref("base.group_system")
        cls.g_export = cls.env.ref("base.group_allow_export")
        cls.g_partner = cls.env.ref("base.group_partner_manager")
        cls.g_currency = cls.env.ref("base.group_multi_currency")

        cls.tag_fin = cls.Tag.create({"name": "UTE Finance"})
        cls.tag_sales = cls.Tag.create({"name": "UTE Sales"})

        common = {"company_id": cls.company_a.id, "company_ids": [Command.set(cls.company_a.ids)], "tz": "UTC"}
        cls.user_fin = new_test_user(cls.env, login="ute_t_fin", groups="base.group_user", **common)
        cls.user_sales = new_test_user(cls.env, login="ute_t_sales", groups="base.group_user", **common)
        cls.user_portal = new_test_user(cls.env, login="ute_t_portal", groups="base.group_portal", **common)
        cls.user_fin.tag_ids = cls.tag_fin
        cls.user_sales.tag_ids = cls.tag_sales
        cls.internal_users = cls.user_fin | cls.user_sales

    # ------------------------------------------------------------------
    def _tmpl(self, **vals):
        vals.setdefault("name", "Test Template")
        vals.setdefault("auto_apply", False)
        return self.Template.create(vals)

    def _apply(self, users=None, templates=None, **kw):
        return self.Template._apply_to_users(users or self.internal_users, templates, **kw)

    # ------------------------------------------------------------------
    # Targeting
    # ------------------------------------------------------------------
    def test_01_global_template_targets_internal_users_only(self):
        self._tmpl(scope="global", apply_mode="override", tz_policy="own", tz="Asia/Riyadh")
        self._apply(self.internal_users | self.user_portal)
        self.assertEqual(self.user_fin.tz, "Asia/Riyadh")
        self.assertEqual(self.user_sales.tz, "Asia/Riyadh")
        self.assertEqual(self.user_portal.tz, "UTC", "Portal users are excluded by default")

    def test_02_tag_targeting(self):
        self._tmpl(tag_ids=[Command.set(self.tag_fin.ids)], apply_mode="override",
                   tz_policy="own", tz="Asia/Riyadh")
        self._apply()
        self.assertEqual(self.user_fin.tz, "Asia/Riyadh")
        self.assertEqual(self.user_sales.tz, "UTC")

    def test_03_archived_templates_are_ignored(self):
        tmpl = self._tmpl(scope="global", apply_mode="override", tz_policy="own", tz="Asia/Riyadh")
        tmpl.active = False
        self.assertFalse(self._apply())
        self.assertEqual(self.user_fin.tz, "UTC")

    # ------------------------------------------------------------------
    # Precedence & conflict resolution
    # ------------------------------------------------------------------
    def test_10_override_higher_sequence_wins(self):
        glob = self._tmpl(scope="global", sequence=1, apply_mode="override",
                          tz_policy="own", tz="Europe/London")
        fin = self._tmpl(tag_ids=[Command.set(self.tag_fin.ids)], sequence=20, apply_mode="override",
                         tz_policy="own", tz="Asia/Riyadh")
        self._apply()
        self.assertEqual(self.user_fin.tz, "Asia/Riyadh")
        self.assertEqual(self.user_sales.tz, "Europe/London")
        # Swap precedence: the global template now runs last and wins.
        glob.sequence, fin.sequence = 50, 5
        self._apply()
        self.assertEqual(self.user_fin.tz, "Europe/London")

    def test_11_merge_never_overwrites_existing_scalar(self):
        self._tmpl(scope="global", apply_mode="merge", tz_policy="own", tz="Asia/Riyadh")
        self.user_sales.tz = False
        self._apply()
        self.assertEqual(self.user_fin.tz, "UTC", "Merge must keep the user's own value")
        self.assertEqual(self.user_sales.tz, "Asia/Riyadh", "Merge fills empty values")

    def test_12_full_stack_merge_then_override_then_merge(self):
        self._tmpl(scope="global", sequence=1, group_ids_policy="own",
                   group_ids=[Command.set(self.g_partner.ids)])
        self._tmpl(tag_ids=[Command.set(self.tag_fin.ids)], sequence=20, group_ids_policy="own",
                   group_ids_mode="override", group_ids=[Command.set(self.g_export.ids)])
        self._tmpl(scope="global", sequence=30, group_ids_policy="own",
                   group_ids=[Command.set(self.g_currency.ids)])
        self._apply()
        fin_groups, sales_groups = self.user_fin.groups_id, self.user_sales.groups_id
        self.assertNotIn(self.g_partner, fin_groups, "Override at seq 20 resets the baseline grant")
        self.assertIn(self.g_export, fin_groups)
        self.assertIn(self.g_currency, fin_groups, "Later merge adds on top of the override")
        self.assertIn(self.g_partner, sales_groups)
        self.assertIn(self.g_currency, sales_groups)
        self.assertNotIn(self.g_export, sales_groups)

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------
    def test_20_groups_merge_is_additive(self):
        self.user_fin.groups_id = [Command.link(self.g_export.id)]
        self._tmpl(scope="global", group_ids_policy="own", group_ids=[Command.set(self.g_partner.ids)])
        self._apply()
        self.assertIn(self.g_export, self.user_fin.groups_id)
        self.assertIn(self.g_partner, self.user_fin.groups_id)

    def test_21_groups_override_keeps_user_type(self):
        self.user_fin.groups_id = [Command.link(self.g_export.id)]
        self._tmpl(scope="global", apply_mode="override", group_ids_policy="own",
                   group_ids=[Command.set(self.g_partner.ids)])
        self._apply()
        self.assertIn(self.g_partner, self.user_fin.groups_id)
        self.assertNotIn(self.g_export, self.user_fin.groups_id)
        self.assertIn(self.g_user, self.user_fin.groups_id)
        self.assertFalse(self.user_fin.share)

    def test_22_group_revocation(self):
        self.user_fin.groups_id = [Command.link(self.g_export.id)]
        self._tmpl(scope="global", group_remove_ids_policy="own",
                   group_remove_ids=[Command.set(self.g_export.ids)])
        self._apply()
        self.assertNotIn(self.g_export, self.user_fin.groups_id)
        self.assertIn(self.g_user, self.user_fin.groups_id)

    def test_23_user_type_groups_forbidden(self):
        with self.assertRaises(ValidationError):
            self._tmpl(group_ids_policy="own", group_ids=[Command.set(self.g_user.ids)])

    def test_24_admins_are_protected(self):
        admin = new_test_user(self.env, login="ute_t_admin", groups="base.group_user,base.group_system",
                              tz="UTC")
        self._tmpl(scope="global", apply_mode="override", group_ids_policy="own",
                   group_ids=[Command.set(self.g_partner.ids)], tz_policy="own", tz="Asia/Riyadh")
        self._apply(admin)
        self.assertIn(self.g_system, admin.groups_id, "Admin rights must never be stripped")
        self.assertEqual(admin.tz, "Asia/Riyadh", "Preferences still apply to admins")

    def test_25_portal_users_never_receive_groups(self):
        self._tmpl(scope="global", include_portal=True, group_ids_policy="own",
                   group_ids=[Command.set(self.g_partner.ids)], apply_mode="override",
                   tz_policy="own", tz="Asia/Riyadh")
        self._apply(self.user_portal)
        self.assertTrue(self.user_portal.share)
        self.assertNotIn(self.g_partner, self.user_portal.groups_id)
        self.assertEqual(self.user_portal.tz, "Asia/Riyadh")

    # ------------------------------------------------------------------
    # Inheritance
    # ------------------------------------------------------------------
    def test_30_multi_level_inheritance(self):
        grand = self._tmpl(name="Grand", tz_policy="own", tz="Asia/Riyadh",
                           group_ids_policy="own", group_ids=[Command.set(self.g_partner.ids)])
        parent = self._tmpl(name="Parent", parent_id=grand.id, tz_policy="inherit",
                            group_ids_policy="own", group_ids=[Command.set(self.g_currency.ids)])
        child = self._tmpl(name="Child", parent_id=parent.id, tz_policy="inherit",
                           group_ids_policy="inherit", apply_mode="override",
                           tag_ids=[Command.set(self.tag_fin.ids)])
        eff = child._get_effective_values()
        self.assertEqual(eff["tz"]["value"], "Asia/Riyadh")
        self.assertEqual(eff["tz"]["source"], grand)
        self.assertEqual(eff["group_ids"]["value"], self.g_currency.ids)
        self.assertEqual(eff["group_ids"]["source"], parent)
        self.assertNotIn("lang", eff)
        self._apply(templates=child)
        self.assertEqual(self.user_fin.tz, "Asia/Riyadh")
        self.assertIn(self.g_currency, self.user_fin.groups_id)
        self.assertNotIn(self.g_partner, self.user_fin.groups_id)

    def test_31_mode_belongs_to_the_applied_template(self):
        parent = self._tmpl(name="Parent", apply_mode="merge", tz_policy="own", tz="Asia/Riyadh")
        child = self._tmpl(name="Child", parent_id=parent.id, tz_policy="inherit", tz_mode="override")
        self._apply(templates=parent)
        self.assertEqual(self.user_fin.tz, "UTC", "Parent merges: existing tz kept")
        self._apply(templates=child)
        self.assertEqual(self.user_fin.tz, "Asia/Riyadh", "Child overrides with the inherited value")

    def test_32_cycle_detection(self):
        a = self._tmpl(name="A")
        b = self._tmpl(name="B", parent_id=a.id)
        with self.assertRaises(ValidationError):
            a.parent_id = b

    def test_33_inherit_requires_parent(self):
        with self.assertRaises(ValidationError):
            self._tmpl(tz_policy="inherit")

    # ------------------------------------------------------------------
    # Companies
    # ------------------------------------------------------------------
    def test_40_default_company_follows_allowed_companies(self):
        self._tmpl(scope="global", allowed_company_ids_policy="own", allowed_company_ids_mode="override",
                   allowed_company_ids=[Command.set(self.company_b.ids)])
        self._apply(self.user_fin)
        self.assertEqual(self.user_fin.company_ids, self.company_b)
        self.assertEqual(self.user_fin.company_id, self.company_b)

    def test_41_explicit_default_company_is_kept_allowed(self):
        self._tmpl(scope="global", sequence=1, allowed_company_ids_policy="own",
                   allowed_company_ids_mode="override", allowed_company_ids=[Command.set(self.company_b.ids)])
        self._tmpl(scope="global", sequence=2, default_company_id_policy="own",
                   default_company_id_mode="override", default_company_id=self.company_a.id)
        self._apply(self.user_fin)
        self.assertEqual(self.user_fin.company_id, self.company_a)
        self.assertEqual(self.user_fin.company_ids, self.company_a | self.company_b)

    # ------------------------------------------------------------------
    # Automatic application (ORM overrides)
    # ------------------------------------------------------------------
    def test_50_auto_apply_on_create(self):
        self._tmpl(auto_apply=True, tag_ids=[Command.set(self.tag_fin.ids)], apply_mode="override",
                   tz_policy="own", tz="Asia/Riyadh")
        user = self.env["res.users"].create({
            "name": "Auto", "login": "ute_t_auto", "tz": "UTC",
            "tag_ids": [Command.set(self.tag_fin.ids)],
        })
        self.assertEqual(user.tz, "Asia/Riyadh")
        self.assertEqual(len(user.template_log_ids), 1)
        self.assertEqual(user.template_log_ids.trigger, "create")

    def test_51_auto_apply_on_tag_change_both_sides(self):
        auto = self._tmpl(auto_apply=True, tag_ids=[Command.set(self.tag_sales.ids)],
                          apply_mode="override", tz_policy="own", tz="Asia/Riyadh")
        self.user_fin.tag_ids = [Command.link(self.tag_sales.id)]
        self.assertEqual(self.user_fin.tz, "Asia/Riyadh")
        # From the tag side
        auto.tz = "Europe/Berlin"
        self.tag_sales.user_ids = [Command.unlink(self.user_fin.id)]
        self.tag_sales.user_ids = [Command.link(self.user_fin.id)]
        self.assertEqual(self.user_fin.tz, "Europe/Berlin")

    def test_52_no_auto_apply_when_disabled(self):
        self._tmpl(auto_apply=False, tag_ids=[Command.set(self.tag_sales.ids)],
                   apply_mode="override", tz_policy="own", tz="Asia/Riyadh")
        self.user_fin.tag_ids = [Command.link(self.tag_sales.id)]
        self.assertEqual(self.user_fin.tz, "UTC")

    # ------------------------------------------------------------------
    # Bulk wizard, dry run, idempotency
    # ------------------------------------------------------------------
    def test_60_wizard_preview_then_apply(self):
        self._tmpl(tag_ids=[Command.set(self.tag_fin.ids)], apply_mode="override",
                   tz_policy="own", tz="Asia/Riyadh")
        wizard = self.env["res.users.template.apply.wizard"].create({
            "target": "tags", "tag_ids": [Command.set(self.tag_fin.ids)], "strategy": "resolve",
        })
        self.assertEqual(wizard.target_count, 1)
        self.assertIn(self.user_fin.name, wizard.preview_html)
        self.assertEqual(self.user_fin.tz, "UTC", "Preview must not write")
        wizard.action_apply()
        self.assertEqual(self.user_fin.tz, "Asia/Riyadh")
        self.assertEqual(self.user_fin.template_log_ids[:1].trigger, "wizard")

    def test_60b_wizard_preview_through_onchange_resolves_tags(self):
        """Regression: in the form (onchange) the preview must match tags correctly."""
        self._tmpl(scope="global", sequence=1, group_ids_policy="own",
                   group_ids=[Command.set(self.g_export.ids)])
        self._tmpl(tag_ids=[Command.set(self.tag_sales.ids)], sequence=20,
                   group_remove_ids_policy="own", group_remove_ids=[Command.set(self.g_export.ids)])
        self._apply()
        self.assertNotIn(self.g_export, self.user_sales.groups_id)
        with Form(self.env["res.users.template.apply.wizard"]) as wiz_form:
            wiz_form.target = "tags"
            wiz_form.tag_ids.add(self.tag_sales)
            preview = wiz_form.preview_html or ""
        self.assertIn("No changes", preview,
                      "Sales users already match: the preview must not re-add the revoked group")

    def test_61_wizard_selected_strategy_ignores_targeting(self):
        tmpl = self._tmpl(tag_ids=[Command.set(self.tag_fin.ids)], apply_mode="override",
                          tz_policy="own", tz="Asia/Riyadh")
        wizard = self.env["res.users.template.apply.wizard"].create({
            "target": "users", "user_ids": [Command.set(self.user_sales.ids)],
            "strategy": "selected", "template_ids": [Command.set(tmpl.ids)],
        })
        wizard.action_apply()
        self.assertEqual(self.user_sales.tz, "Asia/Riyadh")

    def test_62_engine_is_idempotent(self):
        self._tmpl(scope="global", apply_mode="override", tz_policy="own", tz="Asia/Riyadh",
                   group_ids_policy="own", group_ids=[Command.set(self.g_partner.ids)])
        first = self._apply()
        self.assertEqual(set(first), set(self.internal_users.ids))
        second = self._apply()
        self.assertFalse(second, "Re-applying must produce no changes (implied groups closed)")
        self.assertEqual(len(self.user_fin.template_log_ids), 1)

    def test_63_language(self):
        self.env["res.lang"]._activate_lang("fr_FR")
        self._tmpl(scope="global", apply_mode="override", lang_policy="own", lang="fr_FR")
        self._apply(self.user_fin)
        self.assertEqual(self.user_fin.lang, "fr_FR")
