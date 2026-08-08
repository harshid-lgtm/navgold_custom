Cypress.Commands.add('login', (email, password) => {
	cy.request({
		url: '/api/method/login',
		method: 'POST',
		body: {
			usr: email || 'Administrator',
			pwd: password || Cypress.env('adminPassword') || 'admin',
		},
	});
});
