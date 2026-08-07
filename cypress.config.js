// Plain object instead of cypress' defineConfig: the runner is installed in ../frappe,
// so `require("cypress")` does not resolve from this app.
module.exports = {
	adminPassword: 'admin',
	defaultCommandTimeout: 20000,
	pageLoadTimeout: 15000,
	video: false,
	e2e: {
		// bench run-ui-tests overrides this through CYPRESS_baseUrl.
		baseUrl: 'http://test_site:8000',
		specPattern: 'cypress/integration/*.js',
		supportFile: 'cypress/support/e2e.js',
		testIsolation: false,
	},
};
