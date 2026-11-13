// Static marketing site for Relay. Deploy with: cf deploy public
export default {
	worker: {
		name: "relay-site",
		compatibilityDate: "2026-11-10",
		domains: ["relaychurchsystems.com"],
	},
};
