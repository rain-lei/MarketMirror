import { createApp } from "vue";
import {
	Alert,
	Button,
	Card,
	Descriptions,
	Divider,
	Empty,
	Form,
	Input,
	Layout,
	List,
	Menu,
	Progress,
	Select,
	Space,
	Statistic,
	Timeline
} from "ant-design-vue";
import "ant-design-vue/dist/reset.css";
import App from "./App.vue";
import "./styles/app.css";

const app = createApp(App);

[Alert, Button, Card, Descriptions, Divider, Empty, Form, Input, Layout, List, Menu, Progress, Select, Space, Statistic, Timeline].forEach(
	(component) => {
		app.use(component);
	}
);

app.mount("#app");
