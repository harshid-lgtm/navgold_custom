const BUTTON = '.pick-list-scan-action';
const MOBILE = [390, 844];
const DESKTOP = [1440, 900];

// The desk hides header custom buttons below 992px, so below that width the scan action
// is rendered as a button pinned to the bottom of the viewport instead.
describe('Pick List mobile scan button', () => {
	before(() => {
		cy.login();
	});

	it('is pinned to the bottom, full width and big enough to tap', () => {
		open_pick_list(MOBILE);

		cy.get(BUTTON).should('be.visible').and('have.text', 'Scan Tote');
		cy.get(BUTTON).should('have.css', 'position', 'fixed');

		cy.get(BUTTON).then(($button) => {
			const rect = $button[0].getBoundingClientRect();
			// Regression guard: the desk's compact .btn padding otherwise keeps this at 32px.
			expect(rect.height).to.be.at.least(44);
			expect(rect.width).to.be.at.least(MOBILE[0] * 0.8);
			expect(MOBILE[1] - rect.bottom).to.be.at.most(32);
		});
	});

	it('follows the tote state and opens the scan dialog', () => {
		open_pick_list(MOBILE);
		set_tote('TOTE-UI-TEST');
		cy.get(BUTTON).should('have.text', 'Scan Item');

		cy.get(BUTTON).click();
		cy.get('.modal.show .modal-title').should('contain', 'Scan Item Barcode');
	});

	it('keeps toasts clear of the button', () => {
		open_pick_list(MOBILE);
		cy.window().then((win) => {
			win.frappe.show_alert({ message: 'Tote set', indicator: 'green' }, 30);
		});

		cy.get('.es-toast').should('be.visible');
		cy.get(BUTTON).then(($button) => {
			const button_top = $button[0].getBoundingClientRect().top;
			cy.get('.es-toast-container').then(($toast) => {
				expect($toast[0].getBoundingClientRect().bottom).to.be.at.most(
					button_top,
				);
			});
		});
	});

	it('is not rendered without a pickable row', () => {
		open_pick_list(MOBILE);
		set_locations([]);
		cy.get(BUTTON).should('not.exist');
	});

	it('is hidden on desktop, where the header button takes over', () => {
		open_pick_list(DESKTOP);

		cy.get(BUTTON).should('not.be.visible');
		cy.get('.custom-actions button:visible').should('contain', 'Scan Tote');
	});
});

// The desk reads the viewport once at boot, both to collapse its sidebar and to pick the
// container for custom buttons, so every case starts from a fresh load at its own width.
function open_pick_list(viewport) {
	cy.viewport(...viewport);
	cy.visit('/app/pick-list/new');
	cy.window().its('cur_frm.doc.doctype').should('eq', 'Pick List');
	set_locations([{ item_code: 'ITEM-UI-TEST', qty: 1, stock_qty: 1 }]);
}

function set_locations(rows) {
	cy.window().then((win) => {
		const frm = win.cur_frm;
		frm.doc.locations = [];
		for (const row of rows) {
			frm.add_child('locations', row);
		}
		frm.refresh();
	});
}

function set_tote(tote) {
	cy.window().then((win) => {
		win.cur_frm.doc.custom_tote = tote;
		win.cur_frm.refresh();
	});
}
