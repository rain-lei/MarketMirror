import { createApp } from 'vue'
import {
  Alert,
  Avatar,
  Breadcrumb,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Divider,
  Dropdown,
  Empty,
  Form,
  Input,
  Layout,
  List,
  Menu,
  Progress,
  Radio,
  Select,
  Space,
  Statistic,
  Switch,
  Tag,
  Timeline,
  Tooltip,
} from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import App from './App.vue'
import './styles/app.css'

const app = createApp(App)

const components = [
  Alert, Avatar, Breadcrumb, Button, Card, Checkbox,
  Descriptions, Divider, Dropdown, Empty, Form, Input,
  Layout, List, Menu, Progress, Radio, Select, Space,
  Statistic, Switch, Tag, Timeline, Tooltip,
]

components.forEach((component) => app.use(component))

app.mount('#app')
