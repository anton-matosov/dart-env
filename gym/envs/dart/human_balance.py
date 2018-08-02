__author__ = 'yuwenhao'

import numpy as np
from gym import utils
from gym.envs.dart import dart_env

class DartHumanBalanceEnv(dart_env.DartEnv, utils.EzPickle):
    def __init__(self):
        # define the control signal bounds
        self.control_bounds = np.array([[1.0] * 23, [-1.0] * 23])

        # define the observation dimension
        obs_dim = 56

        # initialize pydart world
        dart_env.DartEnv.__init__(self, 'kima/kima_human_balance.skel', 15, obs_dim, self.control_bounds,
                                      disableViewer=True, dt=0.002)

        # define the perturbation model
        self.push_direction = np.array([1, 0, 0])
        self.push_strength = 100.0
        self.push_target = 'head'

        # scales for the control signal
        self.action_scale = 200

        # enable self-collision check
        self.robot_skeleton.set_self_collision_check(True)

        # initialize EzPickle s.t. pickle/joblib can work with pydart correctly
        utils.EzPickle.__init__(self)

    def do_simulation(self, tau, n_frames):
        for _ in range(n_frames):
            if self.cur_step < 30:
                self.robot_skeleton.bodynode(self.push_target).add_ext_force(self.push_direction * self.push_strength)
            self.robot_skeleton.set_forces(tau)
            self.dart_world.step()

    def advance(self, clamped_control):
        tau = np.zeros(self.robot_skeleton.ndofs)
        tau[6:] = clamped_control * self.action_scale

        self.do_simulation(tau, self.frame_skip)

    def step(self, a):
        self.advance(np.clip(a, -1, 1))

        height = self.robot_skeleton.bodynode('head').com()[1]

        alive_bonus = 3.0

        current_q = self.robot_skeleton.q
        q_dev = np.exp(-np.square(np.clip(current_q,-10, 10)).sum())

        reward = alive_bonus - np.square(a).sum()*0.1 + q_dev * 5

        self.cur_step += 1

        s = self.state_vector()

        if not (np.abs(s) < 100).all():
            reward = 0.0

        done = not (np.isfinite(s).all() and (np.abs(s) < 100).all() and
                    (height - self.init_height > -0.35) and
                    np.abs(self.robot_skeleton.q[5]) < 1.0 and np.abs(self.robot_skeleton.q[4]) < 1.0 and
                    np.abs(self.robot_skeleton.q[3]) < 1.0)

        ob = self._get_obs()

        return ob, reward, done, {}

    def _get_obs(self):
        state = np.concatenate([
            self.robot_skeleton.q[[1]],
            self.robot_skeleton.q[3:],
            self.robot_skeleton.dq,
        ])

        return state

    def reset_model(self):
        self.dart_world.reset()

        qpos = self.robot_skeleton.q + self.np_random.uniform(low=-.005, high=.005, size=self.robot_skeleton.ndofs)
        qvel = self.robot_skeleton.dq + self.np_random.uniform(low=-.05, high=.05, size=self.robot_skeleton.ndofs)

        self.set_state(qpos, qvel)
        self.cur_step = 0

        self.init_height = self.robot_skeleton.bodynode('head').com()[1]

        self.push_direction = np.array([np.random.uniform(-1, 1), 0.0, np.random.uniform(-1, 1)])
        self.push_direction /= np.linalg.norm(self.push_direction)
        self.push_strength = (np.random.random() + 1.0) * 100

        return self._get_obs()

    def viewer_setup(self):
        if not self.disableViewer:
            # self.track_skeleton_id = 0
            self._get_viewer().scene.tb.trans[2] = -5.5
